"""Frontier: the only stateful agent. Its state is web.crawl_* in Postgres, so it can restart at any time.

Every tick it
  1. moves due retries back onto scrape:requests,
  2. promotes discovered URLs to fetch jobs within each domain's page/depth budget (site plugins may override),
  3. hands finished domains (nothing discovered or queued) to the enricher,
  4. closes finished campaigns and queues the `websites.profiles` pipeline, which carries the collected facts
     through Bronze and Silver into Gold (gold.domain_profiles) like any other source,
and, when the whole pipeline is idle, re-publishes jobs that were lost (e.g. Redis restarted without its data).
Only one frontier runs at a time (Postgres advisory lock).
"""

from __future__ import annotations

import asyncio
import time

from .. import db
from ..db import Jsonb
from ..observability import get_logger
from . import blobstore, sites
from .messages import (ENRICH_REQUESTS, ENRICH_RESULTS, FETCHED, PARSED, REQUESTS, RETRY, EnrichRequest,
                       FetchRequest, request_id)
from .settings import get_scrape_settings
from .streams import connect_sync, move_due_retries, publish_sync

log = get_logger(__name__)

TICK_SECONDS = 2
RECOVER_EVERY_SECONDS = 60
SWEEP_EVERY_SECONDS = 600
LOST_AFTER = "2 minutes"
CHUNK = 2000
PIPELINE_STREAMS = (REQUESTS, FETCHED, PARSED, ENRICH_REQUESTS, ENRICH_RESULTS)


def seed(country: str, *, limit: int | None = None, domains: list[str] | None = None, max_pages: int | None = None,
         enrich: bool = True, only_live: bool = True, ingest: bool = True) -> dict:
    """Create a campaign for explicit domains, or the top business-candidate domains of gold.domains.

    `ingest`: queue the websites.profiles pipeline when the campaign completes.
    """
    params = {"limit": limit, "domains": domains, "max_pages": max_pages, "enrich": enrich, "only_live": only_live,
              "ingest": ingest}
    with db.serving() as conn, conn.transaction():
        if domains:
            names = sorted({d.strip().lower() for d in domains if d.strip()})
        else:
            names = [row["domain"] for row in conn.execute(f"""
                SELECT domain FROM gold.domains
                WHERE country_code = %s AND is_business_candidate {"AND homepage_status = 200" if only_live else ""}
                ORDER BY crawl_pages DESC NULLS LAST, domain LIMIT %s
            """, [country, limit]).fetchall()]
        campaign = conn.execute("""
            INSERT INTO web.crawl_campaigns (country_code, params, domain_count) VALUES (%s, %s, %s) RETURNING *
        """, [country, Jsonb(params), len(names)]).fetchone()
        with conn.cursor() as cur:
            cur.executemany("INSERT INTO web.crawl_domains (campaign_id, domain, country_code) VALUES (%s, %s, %s)",
                            [[campaign["id"], name, country] for name in names])
            cur.executemany("""
                INSERT INTO web.crawl_urls (campaign_id, url, domain, kind, depth, priority, state)
                VALUES (%s, %s, %s, %s, 0, %s, 'discovered') ON CONFLICT DO NOTHING
            """, [[campaign["id"], url, name, kind, i]
                  for name in names for i, (url, kind) in enumerate(sites.site_for(name).seeds(name))])
    log.info("campaign seeded", campaign_id=campaign["id"], domains=len(names))
    return campaign


def ingest(country: str, campaign_id: int | None = None) -> dict | None:
    """Queue the websites.profiles pipeline (Bronze -> Silver -> Gold -> serving) for what was collected."""
    from ..engine.service import start_run
    from ..sources.websites.adapter import PIPELINE_ID

    try:
        run = start_run(PIPELINE_ID, {"country": country}, trigger_type="api",
                        triggered_by=f"scrape-campaign-{campaign_id}" if campaign_id else "scrape")
    except Exception:
        # The crawl result is safe in web.*; the pipeline can be started by hand (make pipeline-websites).
        log.exception("could not queue the website profiles pipeline", country=country)
        return None
    log.info("queued website profiles pipeline", run_id=run["id"], country=country)
    return run


def cancel(campaign_id: int) -> None:
    with db.serving() as conn:
        conn.execute("UPDATE web.crawl_campaigns SET status = 'cancelled', finished_at = now() "
                     "WHERE id = %s AND status = 'running'", [campaign_id])


class Frontier:
    def __init__(self):
        self.r = connect_sync()
        self.settings = get_scrape_settings()
        self._last_recover = self._last_sweep = 0.0

    def tick(self) -> dict:
        stats = {"retried": move_due_retries(self.r, REQUESTS), "queued": self.promote(),
                 "finished_domains": self.finish_domains(), "finished_campaigns": self.finish_campaigns()}
        now = time.monotonic()
        if now - self._last_recover >= RECOVER_EVERY_SECONDS:
            self._last_recover = now
            stats["recovered"] = self.recover_if_idle()
        if now - self._last_sweep >= SWEEP_EVERY_SECONDS:
            self._last_sweep = now
            blobstore.sweep()
        return stats

    def _budget(self, domain: str, params: dict) -> tuple[int, int]:
        site = sites.site_for(domain)
        return (site.max_pages or params.get("max_pages") or self.settings.max_pages_per_domain,
                site.max_depth if site.max_depth is not None else self.settings.max_depth)

    def promote(self) -> int:
        with db.serving() as conn, conn.transaction():
            rows = conn.execute("""
                SELECT u.campaign_id, u.url, u.domain, u.kind, u.depth, d.country_code, c.params
                FROM web.crawl_urls u
                JOIN web.crawl_domains d ON d.campaign_id = u.campaign_id AND d.domain = u.domain
                JOIN web.crawl_campaigns c ON c.id = u.campaign_id
                WHERE u.state = 'discovered' AND c.status = 'running'
                ORDER BY u.campaign_id, u.domain, u.depth, u.priority, u.url
                LIMIT %s FOR UPDATE OF u SKIP LOCKED
            """, [CHUNK]).fetchall()
            if not rows:
                return 0
            used = {(row["campaign_id"], row["domain"]): row["n"] for row in conn.execute("""
                SELECT campaign_id, domain, count(*) AS n FROM web.crawl_urls
                WHERE campaign_id = ANY(%s) AND domain = ANY(%s) AND state IN ('queued', 'written', 'failed')
                GROUP BY campaign_id, domain
            """, [list({r["campaign_id"] for r in rows}), list({r["domain"] for r in rows})]).fetchall()}
            queue, skip = [], []
            for row in rows:
                key = (row["campaign_id"], row["domain"])
                max_pages, max_depth = self._budget(row["domain"], row["params"])
                if row["depth"] > max_depth or used.get(key, 0) >= max_pages:
                    skip.append(row)
                else:
                    used[key] = used.get(key, 0) + 1
                    queue.append(row)
            self._set_state(conn, skip, "skipped")
            self._set_state(conn, queue, "queued")
            # Publish before commit: if the commit fails the jobs are fetched again later, which is harmless.
            publish_sync(self.r, *(self._request(row) for row in queue))
        if queue:
            log.info("queued fetch jobs", count=len(queue), skipped=len(skip))
        return len(queue)

    @staticmethod
    def _set_state(conn, rows: list[dict], state: str) -> None:
        if rows:
            conn.execute("""
                UPDATE web.crawl_urls u SET state = %s, updated_at = now(),
                       queued_at = CASE WHEN %s = 'queued' THEN now() ELSE u.queued_at END
                FROM unnest(%s::bigint[], %s::text[]) AS t(campaign_id, url)
                WHERE u.campaign_id = t.campaign_id AND u.url = t.url
            """, [state, state, [r["campaign_id"] for r in rows], [r["url"] for r in rows]])

    @staticmethod
    def _request(row: dict) -> FetchRequest:
        return FetchRequest(request_id=request_id(row["campaign_id"], row["url"]), campaign_id=row["campaign_id"],
                            domain=row["domain"], country=row["country_code"], url=row["url"], kind=row["kind"],
                            depth=row["depth"], site=sites.site_for(row["domain"]).name)

    def finish_domains(self) -> int:
        with db.serving() as conn, conn.transaction():
            rows = conn.execute("""
                SELECT d.campaign_id, d.domain, d.country_code, c.params FROM web.crawl_domains d
                JOIN web.crawl_campaigns c ON c.id = d.campaign_id
                WHERE d.state = 'crawling' AND c.status = 'running' AND NOT EXISTS (
                    SELECT 1 FROM web.crawl_urls u WHERE u.campaign_id = d.campaign_id AND u.domain = d.domain
                      AND u.state IN ('discovered', 'queued'))
                LIMIT %s FOR UPDATE OF d SKIP LOCKED
            """, [CHUNK]).fetchall()
            enrich = [r for r in rows if r["params"].get("enrich", True)]
            direct = [r for r in rows if not r["params"].get("enrich", True)]
            for state, group in (("enriching", enrich), ("done", direct)):
                if group:
                    conn.execute("""
                        UPDATE web.crawl_domains d SET state = %s, updated_at = now(),
                               enrich_queued_at = CASE WHEN %s = 'enriching' THEN now() END,
                               finished_at = CASE WHEN %s = 'done' THEN now() END
                        FROM unnest(%s::bigint[], %s::text[]) AS t(campaign_id, domain)
                        WHERE d.campaign_id = t.campaign_id AND d.domain = t.domain
                    """, [state, state, state, [r["campaign_id"] for r in group], [r["domain"] for r in group]])
            publish_sync(self.r, *(EnrichRequest(campaign_id=r["campaign_id"], domain=r["domain"],
                                                 country=r["country_code"]) for r in enrich))
        return len(rows)

    @staticmethod
    def finish_campaigns() -> int:
        with db.serving() as conn:
            done = conn.execute("""
                UPDATE web.crawl_campaigns c SET status = 'completed', finished_at = now()
                WHERE status = 'running' AND NOT EXISTS (
                    SELECT 1 FROM web.crawl_domains d WHERE d.campaign_id = c.id AND d.state <> 'done')
                RETURNING id, country_code, params
            """).fetchall()
        for row in done:
            log.info("campaign completed", campaign_id=row["id"])
            if row["params"].get("ingest", True):
                ingest(row["country_code"], row["id"])
        return len(done)

    def pipeline_idle(self) -> bool:
        with self.r.pipeline(transaction=False) as pipe:
            for stream in PIPELINE_STREAMS:
                pipe.xlen(stream)
            pipe.zcard(RETRY)
            return not any(pipe.execute())

    def recover_if_idle(self) -> int:
        """Nothing is in flight anywhere, yet Postgres says jobs are queued: they were lost. Publish them again."""
        if not self.pipeline_idle():
            return 0
        with db.serving() as conn, conn.transaction():
            urls = conn.execute(f"""
                UPDATE web.crawl_urls u SET queued_at = now() FROM web.crawl_domains d, web.crawl_campaigns c
                WHERE u.state = 'queued' AND u.queued_at < now() - interval '{LOST_AFTER}'
                  AND d.campaign_id = u.campaign_id AND d.domain = u.domain
                  AND c.id = u.campaign_id AND c.status = 'running'
                RETURNING u.campaign_id, u.url, u.domain, u.kind, u.depth, d.country_code
            """).fetchall()
            domains = conn.execute(f"""
                UPDATE web.crawl_domains d SET enrich_queued_at = now() FROM web.crawl_campaigns c
                WHERE d.state = 'enriching' AND d.enrich_queued_at < now() - interval '{LOST_AFTER}'
                  AND c.id = d.campaign_id AND c.status = 'running'
                RETURNING d.campaign_id, d.domain, d.country_code
            """).fetchall()
            publish_sync(self.r, *(self._request(row) for row in urls),
                         *(EnrichRequest(campaign_id=r["campaign_id"], domain=r["domain"], country=r["country_code"])
                           for r in domains))
        if urls or domains:
            log.warning("re-published lost jobs", fetches=len(urls), enrichments=len(domains))
        return len(urls) + len(domains)


async def run(stop: asyncio.Event) -> None:
    lock = db.connect("serving", autocommit=True)
    if not lock.execute("SELECT pg_try_advisory_lock(hashtext('scrape_frontier')) AS ok").fetchone()["ok"]:
        lock.close()
        raise SystemExit("another frontier is already running")
    frontier = Frontier()
    log.info("frontier started")
    try:
        while not stop.is_set():
            await asyncio.to_thread(frontier.tick)
            try:
                await asyncio.wait_for(stop.wait(), timeout=TICK_SECONDS)
            except TimeoutError:
                pass
    finally:
        frontier.r.close()
        lock.close()
        log.info("frontier stopped")
