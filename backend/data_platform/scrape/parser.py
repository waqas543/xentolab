"""Parser agent: FetchResult -> read blob -> extract -> ParsedPage.

Stateless and side-effect free apart from publishing: it never deletes the blob (the writer does, after the
page is committed) and never touches Postgres. Failed fetches pass through so the writer records them.
"""

from __future__ import annotations

import asyncio

from ..observability import get_logger
from . import blobstore, sites
from .extract import decode, parse_html
from .messages import FETCHED, FetchResult, ParsedPage
from .streams import Delivery, StreamWorker, connect, publish

log = get_logger(__name__)

GROUP = "parser"
PARSER_VERSION = "p1"


def parse(result: FetchResult) -> ParsedPage:
    site = sites.get(result.site)
    page = ParsedPage(request_id=result.request_id, campaign_id=result.campaign_id, domain=result.domain,
                      country=result.country, url=result.url, kind=result.kind, depth=result.depth,
                      attempt=result.attempt, fetched_at=result.fetched_at, site=site.name,
                      parser_version=PARSER_VERSION + site.parser_tag,
                      final_url=result.final_url, status=result.status, blob_key=result.blob_key,
                      sha256=result.sha256, bytes=result.bytes,
                      error_class=result.error_class, error_message=result.error_message)
    if result.error_class or not result.blob_key:
        return page
    try:
        body = blobstore.get(result.blob_key)
    except FileNotFoundError:
        page.error_class, page.error_message = "blob_missing", result.blob_key
        return page
    extracted = parse_html(decode(body, result.content_type), url=result.final_url or result.url,
                           domain=result.domain, country=result.country, kind=result.kind, site=site)
    page.title, page.lang, page.text = extracted["title"], extracted["lang"], extracted["text"]
    page.facts, page.links = extracted["facts"], extracted["links"]
    return page


async def run(stop: asyncio.Event, concurrency: int = 4) -> None:
    r = connect()

    async def handle(delivery: Delivery) -> None:
        # Parsing is CPU work: keep it off the event loop so reading/acking stays responsive.
        page = await asyncio.to_thread(parse, FetchResult.from_json(delivery.raw))
        await publish(r, page)

    try:
        await StreamWorker(r, FETCHED, GROUP, handle=handle, concurrency=concurrency).run(stop)
    finally:
        await r.aclose()
