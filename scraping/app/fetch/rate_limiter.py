"""Async rate limiter: evenly spaced request starts (requests/second) plus a concurrency cap.

A 429 from upstream calls `penalize()`, which pauses every caller sharing the limiter, not just the one
that was throttled.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from types import TracebackType


class RateLimiter:
    def __init__(self, requests_per_second: float, max_concurrency: int = 1, *,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        if requests_per_second <= 0:
            raise ValueError("requests_per_second must be > 0")
        self.interval = 1.0 / requests_per_second
        self.max_concurrency = max_concurrency
        self._clock = clock
        self._sleep = sleep
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._next_slot = 0.0
        self._paused_until = 0.0

    async def acquire(self) -> None:
        await self._semaphore.acquire()
        try:
            # Reserve the next start slot synchronously (no await in between), then sleep until it arrives.
            now = self._clock()
            start = max(now, self._next_slot, self._paused_until)
            self._next_slot = start + self.interval
            if start > now:
                await self._sleep(start - now)
        except BaseException:
            self._semaphore.release()
            raise

    def release(self) -> None:
        self._semaphore.release()

    def penalize(self, seconds: float) -> None:
        """Pause all new requests for `seconds` (e.g. after a 429)."""
        self._paused_until = max(self._paused_until, self._clock() + seconds)

    async def __aenter__(self) -> RateLimiter:
        await self.acquire()
        return self

    async def __aexit__(self, exc_type: type[BaseException] | None, exc: BaseException | None,
                        tb: TracebackType | None) -> None:
        self.release()
