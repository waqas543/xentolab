"""Search for one company across the configured engines.

    mode: fallback   engines in order; the first that answers wins (a later engine only runs if the earlier
                     ones failed or are cooling down after a block)
    mode: all        every available engine; results are kept per engine and merged for domain detection

An engine that fails is recorded in `SearchOutcome.errors` and, if it was blocked, cools down. The company
fails only when no engine answered. If every engine is cooling down (e.g. Google is the only engine and it
is blocked), the search waits until the first one recovers instead of failing company after company.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from app.core.config import ExtractionRules, JobConfig
from app.core.exceptions import ScrapingError
from app.core.logging import get_logger
from app.core.models import CompanyInput, PageResponse, SearchResult, SearchSummary
from app.core.utils import normalize_url
from app.enrich.search_engines import LinkResolver, SearchEngine, build_query, create_engine
from app.fetch.fetcher import Fetcher

log = get_logger(__name__)


@dataclass
class SearchOutcome:
    query: str
    pages: list[PageResponse] = field(default_factory=list)
    results: list[SearchResult] = field(default_factory=list)
    summaries: list[SearchSummary] = field(default_factory=list)
    engines: list[str] = field(default_factory=list)
    loaders: dict[str, str] = field(default_factory=dict)
    errors: list[tuple[str, ScrapingError]] = field(default_factory=list)

    def unique_results(self) -> list[SearchResult]:
        """Results across engines with each URL once (first engine wins), e.g. for domain detection."""
        seen: dict[str, SearchResult] = {}
        for result in self.results:
            seen.setdefault(normalize_url(result.url) if result.resolved else result.redirect_url or result.url,
                            result)
        return list(seen.values())


class SearchEnricher:
    def __init__(self, config: JobConfig, fetcher: Fetcher, rules: ExtractionRules, *,
                 engine_fetchers: dict[str, Fetcher] | None = None, user_agent: str | None = None,
                 resolver: LinkResolver | None = None,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        self.config = config
        self.mode = config.search.mode
        self._sleep = sleep
        fetchers = engine_fetchers or {}
        self.engines: list[SearchEngine] = [
            create_engine(engine, config, fetchers.get(engine.name, fetcher), rules, user_agent=user_agent,
                          resolver=resolver)
            for engine in config.search.engines]

    async def _candidates(self) -> list[SearchEngine]:
        available = [engine for engine in self.engines if engine.available()]
        if available:
            return available
        first = min(self.engines, key=lambda engine: engine.cooldown_until)
        wait = first.cooldown_until - first.now()
        if wait > 0:
            log.info("search.waiting_for_cooldown", engine=first.name, wait_s=round(wait))
            await self._sleep(wait)
        return [first]

    async def search(self, company: CompanyInput) -> SearchOutcome:
        outcome = SearchOutcome(query=build_query(self.config.search.query_template, company))
        for engine in await self._candidates():
            if self.mode == "fallback" and outcome.engines:
                break
            try:
                result = await engine.search(outcome.query)
            except ScrapingError as exc:
                engine.record_failure(exc)
                outcome.errors.append((engine.name, exc))
                log.warning("search.engine_failed", engine=engine.name, company=company.company_key,
                            error=f"{type(exc).__name__}: {str(exc)[:200]}")
                continue
            engine.record_success()
            outcome.engines.append(engine.name)
            outcome.loaders[engine.name] = result.loader
            outcome.pages.extend(result.pages)
            outcome.results.extend(result.results)
            outcome.summaries.extend(result.summaries)

        if not outcome.engines:
            if len(outcome.errors) == 1:
                raise outcome.errors[0][1]
            raise ScrapingError("every search engine failed: " + "; ".join(
                f"{name}: {type(exc).__name__}: {str(exc)[:150]}" for name, exc in outcome.errors))
        return outcome

    async def aclose(self) -> None:
        for engine in self.engines:
            await engine.aclose()
