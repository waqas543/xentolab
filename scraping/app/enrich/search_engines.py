"""Search engines: URL building, fetching with bot-challenge retries, parsing, and per-engine block cooldown.

Each engine has its own Fetcher (its own loader, rate limit and retries), so a blocked Google pauses only
Google. After a block (bot challenge, or 429 after the loader's retries) the engine cools down for
`cooldown_seconds`, doubling while it stays blocked; SearchEnricher skips engines that are cooling down.
"""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, field
from typing import ClassVar
from urllib.parse import quote_plus

import httpx

from app.core.config import ExtractionRules, JobConfig, SearchEngineConfig
from app.core.exceptions import BlockedError, LoaderError, ScrapingError
from app.core.logging import get_logger
from app.core.models import CompanyInput, PageResponse, SearchPage, SearchResult, SearchSummary
from app.core.utils import host_of, is_http_url, normalize_url, normalize_whitespace
from app.fetch.fetcher import Fetcher
from app.fetch.retry import RetryPolicy
from app.parse import ParseContext
from app.parse.google_parser import GoogleSearchParser
from app.parse.search_result_parser import SearchResultParser

log = get_logger(__name__)
MAX_COOLDOWN_FACTOR = 8


def build_query(template: str, company: CompanyInput) -> str:
    values = defaultdict(str, {
        "name": company.search_name, "full_name": company.resolved_name, "company_name": company.company_name or "",
        "business_name": company.business_name or "", "city": company.city or "", "province": company.province or "",
        "ntn": company.ntn or "", "registration_no": company.registration_no or "",
    })
    return normalize_whitespace(template.format_map(values))


def build_search_url(template: str, query: str, *, region: str, page: int, page_size: int, language: str = "en",
                     page_param: str = "&s={offset}&dc={next}") -> str:
    offset = page * page_size
    url = template.format_map(defaultdict(str, query=quote_plus(query), region=region, language=language,
                                          offset=offset))
    if page and "{offset}" not in template:
        url += page_param.format(offset=offset, next=offset + 1)
    return url


def is_block(error: BaseException) -> bool:
    """A bot challenge, or a 429 the loader gave up on: the engine is pushing back, not broken."""
    return isinstance(error, BlockedError) or (isinstance(error, LoaderError) and error.status_code == 429)


@dataclass
class EngineResult:
    engine: str
    loader: str
    pages: list[PageResponse] = field(default_factory=list)
    results: list[SearchResult] = field(default_factory=list)
    summaries: list[SearchSummary] = field(default_factory=list)


class SearchEngine(ABC):
    name: ClassVar[str]
    url_template: ClassVar[str]
    region: ClassVar[str]
    page_param: ClassVar[str]

    def __init__(self, config: SearchEngineConfig, job: JobConfig, fetcher: Fetcher, rules: ExtractionRules,
                 *, clock=time.monotonic) -> None:
        self.config = config
        self.fetcher = fetcher
        self.retry = RetryPolicy.from_config(config.retry or job.retry)
        self.limit = job.max_results_per_company
        self.context = ParseContext(rules=rules, config=job)
        self._clock = clock
        self._blocks = 0
        self.cooldown_until = 0.0

    # ------------------------------------------------------------ cooldown

    def now(self) -> float:
        return self._clock()

    def available(self) -> bool:
        return self._clock() >= self.cooldown_until

    def record_failure(self, error: BaseException) -> None:
        if not is_block(error) or not self.config.cooldown_seconds:
            return
        self._blocks += 1
        pause = self.config.cooldown_seconds * min(MAX_COOLDOWN_FACTOR, 2 ** (self._blocks - 1))
        self.cooldown_until = self._clock() + pause
        log.warning("search.engine_cooldown", engine=self.name, pause_s=round(pause), blocks=self._blocks)

    def record_success(self) -> None:
        self._blocks = 0

    # ------------------------------------------------------------ search

    def build_url(self, query: str, page: int) -> str:
        return build_search_url(self.config.url_template or self.url_template, query,
                                region=self.config.region or self.region, page=page,
                                page_size=self.config.page_size, language=self.config.language,
                                page_param=self.page_param)

    @abstractmethod
    def parse(self, page: PageResponse) -> SearchPage:
        """Parse one result page. Raise BlockedError for bot challenges, ParseError for unknown layouts."""

    async def finalize(self, result: EngineResult) -> None:  # noqa: B027 - optional hook
        """Post-process a finished search (e.g. resolve redirect links)."""

    async def search(self, query: str) -> EngineResult:
        result = EngineResult(engine=self.name, loader=self.fetcher.loader_name)
        seen: set[str] = set()
        for page_number in range(self.config.max_pages):
            page, parsed = await self._fetch_and_parse(self.build_url(query, page_number))
            result.pages.append(page)
            result.summaries.extend(parsed.summaries)
            new = [r for r in parsed.results if normalize_url(r.url) not in seen or not r.resolved]
            for item in new:
                seen.add(normalize_url(item.url))
                result.results.append(item.model_copy(update={"rank": len(result.results) + 1}))
            if len(result.results) >= self.limit or len(parsed.results) < self.config.page_size or not new:
                break
        result.results = result.results[:self.limit]
        await self.finalize(result)
        return result

    async def _fetch_and_parse(self, url: str) -> tuple[PageResponse, SearchPage]:
        """Fetch + parse one result page. A bot challenge pauses this engine's requests, then retries uncached."""
        async for attempt in self.retry.retrying():
            with attempt:
                number = attempt.retry_state.attempt_number
                page = await self.fetcher.fetch(url, use_cache=False)
                try:
                    return page, self.parse(page)
                except BlockedError:
                    pause = self.retry.wait_seconds(number)
                    self.fetcher.penalize(pause)
                    log.warning("search.blocked", engine=self.name, url=url, attempt=number, pause_s=round(pause, 1))
                    raise
        raise AssertionError("unreachable")  # retrying() re-raises the last error

    async def aclose(self) -> None:  # noqa: B027 - optional hook
        pass


class DuckDuckGoEngine(SearchEngine):
    name = "duckduckgo"
    url_template = "https://html.duckduckgo.com/html/?q={query}&kl={region}"
    region = "pk-en"
    page_param = "&s={offset}&dc={next}"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.parser = SearchResultParser(self.context)

    def parse(self, page: PageResponse) -> SearchPage:
        return SearchPage(results=self.parser.parse(page))


class GoogleEngine(SearchEngine):
    name = "google"
    url_template = "https://www.google.com/search?q={query}&hl={language}&gl=pk"
    region = "pk"
    page_param = "&start={offset}"

    def __init__(self, *args, resolver: LinkResolver | None = None, user_agent: str | None = None,
                 **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.parser = GoogleSearchParser(self.context)
        self.resolver = resolver or (LinkResolver(user_agent) if self.config.resolve_links else None)

    def parse(self, page: PageResponse) -> SearchPage:
        return self.parser.parse(page)

    async def finalize(self, result: EngineResult) -> None:
        if self.resolver is None:
            return
        links = [r.redirect_url for r in result.results if r.redirect_url and not r.resolved]
        links += [s.redirect_url for summary in result.summaries for s in summary.sources
                  if s.redirect_url and not s.url]
        resolved = await self.resolver.resolve_all(links)
        results: list[SearchResult] = []
        seen: set[str] = set()
        for item in result.results:
            if (url := resolved.get(item.redirect_url or "")) is not None:
                item = item.model_copy(update={"url": url, "domain": host_of(url), "resolved": True})
            key = normalize_url(item.url)
            if item.resolved and key in seen:
                continue
            seen.add(key)
            results.append(item.model_copy(update={"rank": len(results) + 1}))
        result.results = results
        for summary in result.summaries:
            for source in summary.sources:
                if source.url is None and (url := resolved.get(source.redirect_url or "")):
                    source.url = url
            unique_sources = {}
            for source in summary.sources:
                unique_sources.setdefault(normalize_url(source.url) if source.url else id(source), source)
            summary.sources = list(unique_sources.values())

    async def aclose(self) -> None:
        if self.resolver is not None:
            await self.resolver.aclose()


class LinkResolver:
    """Resolves Google's `/goto?url=<token>` links with a plain GET (no browser): the 302 Location is the target.
    Cheap and, so far, not throttled like result pages. A failure just leaves the link unresolved."""

    def __init__(self, user_agent: str | None = None, *, concurrency: int = 4, timeout: float = 15.0,
                 client: httpx.AsyncClient | None = None, cache_size: int = 2048) -> None:
        headers = {"User-Agent": user_agent or "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 "
                                               "(KHTML, like Gecko) Chrome/141.0 Safari/537.36"}
        self._client = client or httpx.AsyncClient(headers=headers, timeout=timeout, follow_redirects=False)
        self._owns_client = client is None
        self._semaphore = asyncio.Semaphore(concurrency)
        self._cache: OrderedDict[str, str] = OrderedDict()
        self._cache_size = cache_size

    async def resolve(self, url: str) -> str | None:
        if url in self._cache:
            return self._cache[url]
        async with self._semaphore:
            try:
                response = await self._client.get(url)
            except httpx.HTTPError as exc:
                log.debug("link.resolve_failed", url=url, error=str(exc))
                return None
        location = response.headers.get("location", "")
        if response.status_code not in (301, 302, 303, 307, 308) or not is_http_url(location):
            log.debug("link.unresolved", url=url, status=response.status_code)
            return None
        target = location.split("#:~:", 1)[0]
        self._cache[url] = target
        if len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
        return target

    async def resolve_all(self, urls: list[str]) -> dict[str, str]:
        unique_urls = list(dict.fromkeys(urls))
        targets = await asyncio.gather(*(self.resolve(url) for url in unique_urls))
        return {url: target for url, target in zip(unique_urls, targets, strict=True) if target}

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


ENGINES: dict[str, type[SearchEngine]] = {DuckDuckGoEngine.name: DuckDuckGoEngine, GoogleEngine.name: GoogleEngine}


def create_engine(config: SearchEngineConfig, job: JobConfig, fetcher: Fetcher, rules: ExtractionRules, *,
                  user_agent: str | None = None, resolver: LinkResolver | None = None,
                  clock=time.monotonic) -> SearchEngine:
    if config.name not in ENGINES:
        raise ScrapingError(f"unknown search engine {config.name!r}; available: {', '.join(sorted(ENGINES))}")
    if config.name == GoogleEngine.name:
        return GoogleEngine(config, job, fetcher, rules, clock=clock, resolver=resolver, user_agent=user_agent)
    return ENGINES[config.name](config, job, fetcher, rules, clock=clock)
