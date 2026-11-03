"""Writer agent: the only agent that writes crawl results to Postgres.

scrape:parsed  -> web.crawl_urls state, web.pages, web.facts, newly discovered links; then deletes the blobs.
enrich:results -> LLM facts, domain marked done.

Profiles are not built here: the `websites.profiles` pipeline ingests web.facts into Bronze/Silver and the Gold
build merges them with every other source (see sources/websites and gold/domain_profiles.py).

Batches are written in one transaction and every write is idempotent (a redelivered message rewrites the same
rows), so at-least-once delivery is safe. Blobs are deleted only after the commit.
"""

from __future__ import annotations

import asyncio

from .. import db
from ..observability import get_logger
from . import blobstore
from .messages import ENRICH_RESULTS, PARSED, EnrichResult, ParsedPage
from .streams import Delivery, StreamWorker, connect

log = get_logger(__name__)

GROUP = "writer"


def write_pages(pages: list[ParsedPage]) -> None:
    with db.serving() as conn, conn.transaction(), conn.cursor() as cur:
        for page in pages:
            state = "failed" if page.error_class else "written"
            cur.execute("""
                UPDATE web.crawl_urls SET state = %s, attempts = %s, http_status = %s, final_url = %s,
                       error_class = %s, error_message = %s, updated_at = now()
                WHERE campaign_id = %s AND url = %s
            """, [state, page.attempt, page.status, page.final_url, page.error_class,
                  (page.error_message or "")[:1000] or None, page.campaign_id, page.url])
            if page.error_class:
                continue
            cur.execute("""
                INSERT INTO web.pages (domain, url, campaign_id, kind, final_url, http_status, title, lang, text,
                                       content_sha256, bytes, parser_version, fetched_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (domain, url) DO UPDATE SET campaign_id = EXCLUDED.campaign_id, kind = EXCLUDED.kind,
                    final_url = EXCLUDED.final_url, http_status = EXCLUDED.http_status, title = EXCLUDED.title,
                    lang = EXCLUDED.lang, text = EXCLUDED.text, content_sha256 = EXCLUDED.content_sha256,
                    bytes = EXCLUDED.bytes, parser_version = EXCLUDED.parser_version,
                    fetched_at = EXCLUDED.fetched_at, written_at = now()
            """, [page.domain, page.url, page.campaign_id, page.kind, page.final_url, page.status, page.title,
                  page.lang, page.text, page.sha256, page.bytes, page.parser_version, page.fetched_at])
            # Facts of a page are replaced as a whole, so re-parsing never duplicates them.
            cur.execute("DELETE FROM web.facts WHERE domain = %s AND url = %s", [page.domain, page.url])
            cur.executemany("""
                INSERT INTO web.facts (domain, url, field, value, source, evidence, confidence, extractor_version,
                                       campaign_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, [[page.domain, page.url, f["field"], f["value"], f["source"], f.get("evidence"),
                   f.get("confidence"), page.parser_version, page.campaign_id] for f in page.facts])
            # Links go into the frontier's table in the same transaction as the page, so the frontier can never
            # see the domain as finished before its follow-up pages are known.
            cur.executemany("""
                INSERT INTO web.crawl_urls (campaign_id, url, domain, kind, depth, priority, from_url, state)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'discovered')
                ON CONFLICT (campaign_id, url) DO NOTHING
            """, [[page.campaign_id, link["url"], page.domain, link["kind"], page.depth + 1,
                   link.get("priority", 5), page.url] for link in page.links])
    blobstore.delete(*(page.blob_key for page in pages))


def write_enrichments(results: list[EnrichResult]) -> None:
    with db.serving() as conn, conn.transaction():
        with conn.cursor() as cur:
            for result in results:
                cur.execute("DELETE FROM web.facts WHERE domain = %s AND source = 'llm'", [result.domain])
                cur.executemany("""
                    INSERT INTO web.facts (domain, url, field, value, source, evidence, confidence, extractor_version,
                                           campaign_id)
                    VALUES (%s, NULL, %s, %s, 'llm', %s, %s, %s, %s)
                """, [[result.domain, f["field"], f["value"], f.get("evidence"), f.get("confidence"),
                       f"{result.model}/{result.prompt_version}", result.campaign_id] for f in result.facts])
                cur.execute("""
                    UPDATE web.crawl_domains SET state = 'done', finished_at = now(), updated_at = now()
                    WHERE campaign_id = %s AND domain = %s
                """, [result.campaign_id, result.domain])
                if result.error_class:
                    log.warning("enrichment failed; the domain keeps its deterministic facts", domain=result.domain,
                                error_class=result.error_class)


async def run(stop: asyncio.Event, batch_size: int = 50) -> None:
    r = connect()

    async def pages(batch: list[Delivery]) -> None:
        await asyncio.to_thread(write_pages, [ParsedPage.from_json(d.raw) for d in batch])

    async def enrichments(batch: list[Delivery]) -> None:
        await asyncio.to_thread(write_enrichments, [EnrichResult.from_json(d.raw) for d in batch])

    try:
        await asyncio.gather(
            StreamWorker(r, PARSED, GROUP, handle_batch=pages, batch_size=batch_size).run(stop),
            StreamWorker(r, ENRICH_RESULTS, GROUP, handle_batch=enrichments, batch_size=10).run(stop))
    finally:
        await r.aclose()
