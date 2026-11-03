"""The PageLoader interface. Concrete loaders implement one attempt (`_fetch_once`); this base class adds
rate limiting, retries, error normalization and logging so every loader behaves the same.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from types import TracebackType
from typing import ClassVar

import httpx
from tenacity import RetryCallState

from app.core.exceptions import LoaderError, RetryableError, TransientLoaderError
from app.core.logging import get_logger
from app.core.models import PageResponse
from app.fetch.rate_limiter import RateLimiter
from app.fetch.retry import RetryPolicy, parse_retry_after

log = get_logger(__name__)


class PageLoader(ABC):
    name: ClassVar[str]

    def __init__(self, *, retry: RetryPolicy | None = None, limiter: RateLimiter | None = None,
                 timeout: float = 90.0, render_js: bool = True) -> None:
        self.retry = retry or RetryPolicy()
        self.limiter = limiter
        self.timeout = timeout
        self.render_js = render_js

    async def load(self, url: str, *, render_js: bool | None = None) -> PageResponse:
        """Load one URL, retrying transient failures. Raises LoaderError when it finally gives up."""
        render = self.render_js if render_js is None else render_js
        attempts = 0
        page: PageResponse | None = None
        try:
            async for attempt in self.retry.retrying(before_sleep=self._log_retry(url)):
                with attempt:
                    attempts = attempt.retry_state.attempt_number
                    page = await self._attempt(url, render)
        except RetryableError as exc:
            status = getattr(exc, "status_code", None)
            log.warning("page.failed", loader=self.name, url=url, attempts=attempts, status=status, error=str(exc))
            raise LoaderError(f"gave up after {attempts} attempt(s): {exc}", url=url, status_code=status,
                              attempts=attempts) from exc
        except LoaderError as exc:
            exc.attempts = attempts
            log.warning("page.failed", loader=self.name, url=url, attempts=attempts, status=exc.status_code,
                        error=str(exc))
            raise
        assert page is not None
        page = page.model_copy(update={"attempts": attempts})
        log.info("page.loaded", loader=self.name, url=url, final_url=page.final_url if page.final_url != url else None,
                 status=page.status_code, latency_ms=page.latency_ms, attempts=attempts, bytes=page.size_bytes)
        return page

    async def _attempt(self, url: str, render_js: bool) -> PageResponse:
        if self.limiter is None:
            return await self._fetch_once(url, render_js=render_js)
        async with self.limiter:
            try:
                return await self._fetch_once(url, render_js=render_js)
            except TransientLoaderError as exc:
                if exc.status_code == 429:
                    self.limiter.penalize(min(exc.retry_after or self.retry.backoff_seconds,
                                              self.retry.max_backoff_seconds))
                raise

    def _log_retry(self, url: str):
        def before_sleep(state: RetryCallState) -> None:
            error = state.outcome.exception() if state.outcome else None
            wait = state.next_action.sleep if state.next_action else None
            log.info("page.retry", loader=self.name, url=url, attempt=state.attempt_number,
                     wait_s=round(wait, 2) if wait is not None else None, error=str(error))
        return before_sleep

    @abstractmethod
    async def _fetch_once(self, url: str, *, render_js: bool) -> PageResponse:
        """One attempt. Raise TransientLoaderError for retryable failures, LoaderError for permanent ones."""

    async def aclose(self) -> None:  # noqa: B027 - optional hook
        pass

    async def __aenter__(self) -> PageLoader:
        return self

    async def __aexit__(self, exc_type: type[BaseException] | None, exc: BaseException | None,
                        tb: TracebackType | None) -> None:
        await self.aclose()


# ---------------------------------------------------------------- shared httpx helpers


def elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def raise_for_transport(exc: httpx.HTTPError, url: str) -> None:
    """Map httpx transport errors onto the retry taxonomy (certificate failures are permanent)."""
    if "CERTIFICATE_VERIFY_FAILED" in str(exc):
        raise LoaderError(f"TLS certificate verification failed: {exc}", url=url) from exc
    if isinstance(exc, httpx.TimeoutException):
        raise TransientLoaderError(f"timeout: {type(exc).__name__}") from exc
    if isinstance(exc, httpx.TransportError):
        raise TransientLoaderError(f"transport error: {type(exc).__name__}: {exc}") from exc
    raise exc


def raise_for_status(response: httpx.Response, url: str, retry_on_status: frozenset[int]) -> None:
    status = response.status_code
    if status < 400:
        return
    detail = response.text[:200].strip().replace("\n", " ")
    if status in retry_on_status:
        raise TransientLoaderError(f"HTTP {status}: {detail}", status_code=status,
                                   retry_after=parse_retry_after(response.headers.get("retry-after")))
    raise LoaderError(f"HTTP {status}: {detail}", url=url, status_code=status)
