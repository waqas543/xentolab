"""Job-facing fetch API: one shared loader and rate limiter per run, run statistics, and a small in-run cache
so the same URL is not paid for twice (e.g. two companies resolving to the same website).
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import asdict, dataclass

from app.core.config import JobConfig, Settings, get_settings
from app.core.exceptions import LoaderError
from app.core.models import PageResponse
from app.core.utils import normalize_url
from app.fetch.rate_limiter import RateLimiter
from app.loader import PageLoader, create_loader


@dataclass
class FetchStats:
    requests: int = 0
    succeeded: int = 0
    failed: int = 0
    retries: int = 0
    cache_hits: int = 0
    bytes: int = 0
    latency_ms_total: int = 0

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


class Fetcher:
    def __init__(self, loader: PageLoader, *, cache_size: int = 64) -> None:
        self.loader = loader
        self.stats = FetchStats()
        self._cache: OrderedDict[tuple[str, bool], PageResponse] = OrderedDict()
        self._cache_size = cache_size
        self._inflight: dict[tuple[str, bool], asyncio.Future[PageResponse]] = {}

    @classmethod
    def from_config(cls, config: JobConfig, settings: Settings | None = None) -> Fetcher:
        limiter = RateLimiter(config.rate_limit.requests_per_second, config.rate_limit.max_concurrency)
        return cls(create_loader(config, settings or get_settings(), limiter=limiter))

    @property
    def loader_name(self) -> str:
        return self.loader.name

    def penalize(self, seconds: float) -> None:
        """Pause every request sharing this fetcher's rate limiter (e.g. after a bot challenge)."""
        if self.loader.limiter is not None:
            self.loader.limiter.penalize(seconds)

    async def fetch(self, url: str, *, render_js: bool | None = None, use_cache: bool = True) -> PageResponse:
        render = self.loader.render_js if render_js is None else render_js
        key = (normalize_url(url), render)
        if use_cache and key in self._cache:
            self._cache.move_to_end(key)
            self.stats.cache_hits += 1
            return self._cache[key]
        if use_cache and key in self._inflight:  # someone is already fetching it: share the result
            self.stats.cache_hits += 1
            return await asyncio.shield(self._inflight[key])

        future: asyncio.Future[PageResponse] = asyncio.get_running_loop().create_future()
        self._inflight[key] = future
        self.stats.requests += 1
        try:
            page = await self.loader.load(url, render_js=render)
        except LoaderError as exc:
            self.stats.failed += 1
            self.stats.retries += max(0, exc.attempts - 1)
            future.set_exception(exc)
            future.exception()  # mark retrieved so asyncio does not warn when nobody else awaited it
            raise
        except BaseException as exc:
            future.cancel()
            raise exc
        finally:
            self._inflight.pop(key, None)

        self.stats.succeeded += 1
        self.stats.retries += page.attempts - 1
        self.stats.bytes += page.size_bytes
        self.stats.latency_ms_total += page.latency_ms
        future.set_result(page)
        self._cache[key] = page
        if len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
        return page

    async def aclose(self) -> None:
        await self.loader.aclose()
