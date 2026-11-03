"""Retry policy built on tenacity: exponential backoff with jitter, honouring Retry-After.

Only `RetryableError` subclasses are retried; anything else (e.g. a 404) fails on the first attempt.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime

from tenacity import AsyncRetrying, RetryCallState, retry_if_exception_type, stop_after_attempt

from app.core.config import RetryConfig
from app.core.exceptions import RetryableError
from app.core.utils import utcnow


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    backoff_seconds: float = 2.0
    max_backoff_seconds: float = 60.0
    retry_on_status: frozenset[int] = field(default_factory=lambda: frozenset({408, 425, 429, 500, 502, 503, 504}))
    jitter: float = 0.25

    @classmethod
    def from_config(cls, config: RetryConfig) -> RetryPolicy:
        return cls(max_attempts=config.max_attempts, backoff_seconds=config.backoff_seconds,
                   max_backoff_seconds=config.max_backoff_seconds, retry_on_status=frozenset(config.retry_on_status))

    def wait_seconds(self, attempt: int, error: BaseException | None = None) -> float:
        """Delay after failed attempt number `attempt` (1-based)."""
        delay = min(self.max_backoff_seconds, self.backoff_seconds * 2 ** (attempt - 1))
        delay += delay * self.jitter * random.random()
        retry_after = getattr(error, "retry_after", None)
        if retry_after:
            delay = max(delay, min(retry_after, self.max_backoff_seconds))
        return delay

    def _wait(self, state: RetryCallState) -> float:
        error = state.outcome.exception() if state.outcome else None
        return self.wait_seconds(state.attempt_number, error)

    def retrying(self, before_sleep: Callable[[RetryCallState], None] | None = None) -> AsyncRetrying:
        return AsyncRetrying(stop=stop_after_attempt(self.max_attempts), wait=self._wait,
                             retry=retry_if_exception_type(RetryableError), reraise=True, before_sleep=before_sleep)


def parse_retry_after(value: str | None) -> float | None:
    """Retry-After header as seconds: either delta-seconds or an HTTP date."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        return max(0.0, (parsedate_to_datetime(value) - utcnow()).total_seconds())
    except (TypeError, ValueError):
        return None
