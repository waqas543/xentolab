"""Policy-driven crawler: breadth-first from seed URLs, following links that pass the policy
(allowed domains, include/exclude regexes, depth and page budget), fetching concurrently through the Fetcher.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass, field

from app.core.config import CrawlConfig, WebsiteConfig
from app.core.exceptions import LoaderError
from app.core.logging import get_logger
from app.core.models import PageResponse
from app.core.utils import is_http_url, registrable_domain
from app.crawl.url_queue import CrawlItem, URLQueue
from app.fetch.fetcher import Fetcher
from app.parse.html_parser import HtmlDocument

log = get_logger(__name__)


@dataclass
class CrawlPolicy:
    max_depth: int = 1
    max_pages: int = 10
    allowed_domains: set[str] = field(default_factory=set)
    include: list[re.Pattern[str]] = field(default_factory=list)
    exclude: list[re.Pattern[str]] = field(default_factory=list)

    @classmethod
    def from_config(cls, config: CrawlConfig | WebsiteConfig, allowed_domains: Iterable[str] = ()) -> CrawlPolicy:
        domains = set(getattr(config, "allowed_domains", []) or []) | set(allowed_domains)
        return cls(max_depth=config.max_depth, max_pages=config.max_pages,
                   allowed_domains={registrable_domain(d) for d in domains},
                   include=[re.compile(p, re.I) for p in config.include_patterns],
                   exclude=[re.compile(p, re.I) for p in config.exclude_patterns])

    def allows(self, url: str) -> bool:
        """Whether a discovered link should be followed (seeds bypass include patterns)."""
        if not is_http_url(url) or any(p.search(url) for p in self.exclude):
            return False
        if self.allowed_domains and registrable_domain(url) not in self.allowed_domains:
            return False
        return not self.include or any(p.search(url) for p in self.include)


@dataclass
class CrawlResult:
    item: CrawlItem
    page: PageResponse | None = None
    error: LoaderError | None = None


class Crawler:
    def __init__(self, fetcher: Fetcher, policy: CrawlPolicy, *, concurrency: int = 2) -> None:
        self.fetcher = fetcher
        self.policy = policy
        self.concurrency = concurrency

    async def crawl(self, seeds: Iterable[str], *, render_js: bool | None = None) -> AsyncIterator[CrawlResult]:
        queue = URLQueue()
        seeds = [s for s in seeds if is_http_url(s)]
        if not self.policy.allowed_domains:
            self.policy.allowed_domains = {registrable_domain(s) for s in seeds}
        for seed in seeds:
            queue.push(seed, depth=0)

        scheduled = 0
        pending: dict[asyncio.Task[PageResponse], CrawlItem] = {}
        try:
            while queue or pending:
                while queue and len(pending) < self.concurrency and scheduled < self.policy.max_pages:
                    item = queue.pop()
                    assert item is not None
                    task = asyncio.create_task(self.fetcher.fetch(item.url, render_js=render_js))
                    pending[task] = item
                    scheduled += 1
                if not pending:
                    break
                done, _ = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    item = pending.pop(task)
                    error = task.exception()
                    if isinstance(error, LoaderError):
                        yield CrawlResult(item=item, error=error)
                        continue
                    if error is not None:
                        raise error
                    page = task.result()
                    if item.depth == 0:  # a seed that redirects (e.g. .com -> .com.pk) brings its new domain along
                        self.policy.allowed_domains.add(registrable_domain(page.final_url))
                    if item.depth < self.policy.max_depth:
                        self._enqueue_links(queue, page, item.depth + 1)
                    yield CrawlResult(item=item, page=page)
        finally:
            for task in pending:
                task.cancel()

    def _enqueue_links(self, queue: URLQueue, page: PageResponse, depth: int) -> None:
        doc = HtmlDocument(page.html, page.final_url)
        added = 0
        for link in doc.links:
            url = link.url.split("#")[0]
            if self.policy.allows(url) and queue.push(url, depth=depth, parent=page.url):
                added += 1
        log.debug("crawl.links", url=page.url, depth=depth, added=added)
