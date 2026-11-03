"""Scraping engine end to end: every agent running against Redis and Postgres, a local website standing in for
a company site, a site plugin, robots.txt, retries and a stubbed LLM. Plus the job-processing guarantees.
Needs local Redis (uses database 15, flushed) and PostgreSQL.
"""

import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urljoin

import pytest

pytestmark = pytest.mark.integration

HOME = """<html lang="en"><head><title>Acme | Home</title>
<script type="application/ld+json">{"@type": "Organization", "name": "Acme Cement", "foundingDate": "1998"}</script>
</head><body><a href="/about-us">About</a> <a href="/contact">Contact</a> <a href="/private/about">Hidden</a>
<a href="https://www.facebook.com/acmepk">Facebook</a> Acme makes cement.</body></html>"""
ABOUT = "<html><body><h1>About Acme</h1><p>Acme was founded in 1998. Call 042 111 222 333.</p></body></html>"


class Site(BaseHTTPRequestHandler):
    hits: dict[str, int] = {}

    def do_GET(self):
        Site.hits[self.path] = Site.hits.get(self.path, 0) + 1
        pages = {"/": (200, HOME), "/about-us": (200, ABOUT), "/robots.txt": (200, "User-agent: *\nDisallow: /private\n")}
        status, body = pages.get(self.path, (503, "down"))
        self.send_response(status)
        self.send_header("Content-Type", "text/plain" if self.path == "/robots.txt" else "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *args):
        pass


@pytest.fixture
def engine(database, tmp_path, monkeypatch):
    import redis

    from data_platform.config import get_settings
    from data_platform.scrape import fetcher, sites, streams
    from data_platform.scrape.settings import get_scrape_settings

    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/15")
    monkeypatch.setenv("SCRAPE_BLOB_DIR", str(tmp_path / "blobs"))
    monkeypatch.setenv("SCRAPE_HOST_DELAY_SECONDS", "0")
    monkeypatch.setenv("SCRAPE_MAX_FETCH_ATTEMPTS", "2")
    monkeypatch.setenv("SCRAPE_CLAIM_IDLE_SECONDS", "0.5")
    monkeypatch.setenv("DP_SECRET_PROXIES_FILE", str(tmp_path / "no-proxies.json"))
    get_settings.cache_clear()
    get_scrape_settings.cache_clear()
    client = redis.from_url("redis://localhost:6379/15")
    try:
        client.flushdb()
    except redis.ConnectionError as error:
        pytest.skip(f"Redis not available: {error}")
    monkeypatch.setattr(fetcher, "RETRY_BASE_SECONDS", 0)
    monkeypatch.setattr(streams, "CLAIM_EVERY_SECONDS", 0)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"

    class LocalSite(sites.Site):
        """A plugin for the test website (an IP address has no registrable domain for the generic rules)."""
        name, domains = "acme-test", ("acme.test",)

        def seeds(self, domain):
            return [(f"{base}/", "home")]

        def follow(self, page_url, hrefs, domain, kind):
            wanted = {"/about-us": "about", "/contact": "contact", "/private/about": "about"}
            return [{"url": urljoin(base, h), "kind": wanted[h], "priority": i}
                    for i, h in enumerate(hrefs) if h in wanted] if kind == "home" else []

    plugin = LocalSite()
    monkeypatch.setattr(sites, "site_for", lambda domain: plugin)
    monkeypatch.setattr(sites, "get", lambda name: plugin)
    Site.hits.clear()
    yield {"base": base, "redis": client, "blobs": tmp_path / "blobs"}
    server.shutdown()
    client.flushdb()
    get_settings.cache_clear()
    get_scrape_settings.cache_clear()


def test_campaign_crawls_parses_enriches_and_queues_the_gold_pipeline(engine, monkeypatch):
    from data_platform import db
    from data_platform.scrape import enricher, fetcher, frontier, parser, writer

    async def fake_llm(self, domain, text):
        assert "About Acme" in text
        return {"company_name": "Acme", "summary": "Acme makes cement.", "industry": "Cement",
                "category_group": "Industry & Craft", "city": None, "founded_year": {"value": 1998,
                "evidence": "Acme was founded in 1998."}, "employees": None, "market_cap": None,
                "financial_info": {"value": "listed on PSX", "evidence": "listed on PSX"}}   # not in the text

    monkeypatch.setattr(enricher.Enricher, "ask", fake_llm)
    ingested = []
    monkeypatch.setattr(frontier, "ingest", lambda country, campaign_id=None: ingested.append((country, campaign_id)))
    with db.serving() as conn:
        conn.execute("TRUNCATE web.crawl_campaigns, web.pages, web.facts CASCADE")
    campaign = frontier.seed("PK", domains=["acme.test"])

    async def main():
        stop = asyncio.Event()
        agents = [asyncio.create_task(run(stop)) for run in
                  (frontier.run, fetcher.run, parser.run, enricher.run, writer.run)]
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            with db.serving() as conn:
                status = conn.execute("SELECT status FROM web.crawl_campaigns WHERE id = %s",
                                      [campaign["id"]]).fetchone()["status"]
            if status == "completed":
                break
            await asyncio.sleep(0.5)
        stop.set()
        await asyncio.gather(*agents)
        return status

    assert asyncio.run(main()) == "completed"

    with db.serving() as conn:
        urls = {r["url"].removeprefix(engine["base"]): r for r in conn.execute(
            "SELECT url, state, attempts, error_class FROM web.crawl_urls WHERE campaign_id = %s",
            [campaign["id"]]).fetchall()}
        facts = {(r["field"], r["value"], r["source"]) for r in conn.execute(
            "SELECT field, value, source FROM web.facts WHERE domain = 'acme.test'").fetchall()}
        pages = conn.execute("SELECT count(*) AS n FROM web.pages WHERE domain = 'acme.test'").fetchone()["n"]

    assert urls["/"]["state"] == urls["/about-us"]["state"] == "written"
    assert (urls["/contact"]["state"], urls["/contact"]["error_class"], urls["/contact"]["attempts"]) == \
        ("failed", "http_5xx", 2)                                  # retried once, then recorded as failed
    assert (urls["/private/about"]["state"], urls["/private/about"]["error_class"]) == ("failed", "robots_denied")
    assert Site.hits.get("/private/about") is None and Site.hits["/contact"] == 2
    assert pages == 2
    assert {("company_name", "Acme Cement", "jsonld"), ("founded_year", "1998", "jsonld"),
            ("social.facebook", "https://www.facebook.com/acmepk", "link"), ("phone", "+9242111222333", "text"),
            ("summary", "Acme makes cement.", "llm"), ("founded_year", "1998", "llm")} <= facts
    assert not any(field == "financial_info" for field, _, _ in facts)   # the LLM's unsupported claim was dropped
    assert ingested == [("PK", campaign["id"])]       # the finished campaign hands its facts to the Gold pipeline
    assert not list(engine["blobs"].rglob("*.zst"))                # raw HTML deleted once written
    assert engine["redis"].xlen("scrape:dlq") == 0


def test_failed_jobs_are_redelivered_and_poison_messages_dead_lettered(engine):
    from data_platform.errors import PermanentError
    from data_platform.scrape import streams

    calls: dict[str, int] = {}

    async def main():
        r = streams.connect()
        for body in ("flaky", "poison", "ok"):
            await r.xadd("test:jobs", {"m": json.dumps(body)})
        stop = asyncio.Event()

        async def handle(delivery):
            body = json.loads(delivery.raw)
            calls[body] = calls.get(body, 0) + 1
            if body == "poison":
                raise PermanentError("cannot be processed")
            if body == "flaky" and calls[body] == 1:
                raise RuntimeError("worker crashed mid-job")

        worker = streams.StreamWorker(r, "test:jobs", "testers", handle=handle, concurrency=2)
        task = asyncio.create_task(worker.run(stop))
        for _ in range(40):
            if calls.get("flaky") == 2 and await r.xlen("test:jobs") == 0:
                break
            await asyncio.sleep(0.25)
        stop.set()
        await task
        dead = await r.xrange("scrape:dlq")
        await r.aclose()
        return dead

    dead = asyncio.run(main())
    assert calls == {"flaky": 2, "poison": 1, "ok": 1}
    assert [entry["m"] for _, entry in dead] == ['"poison"']
