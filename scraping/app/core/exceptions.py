"""Exception hierarchy. Retry logic keys off `RetryableError`; job-level handling keys off `ScrapingError`."""

from __future__ import annotations


class ScrapingError(Exception):
    """Base class for every expected failure in the engine."""


class ConfigError(ScrapingError):
    """Invalid or missing configuration (job YAML, .env)."""


class InputError(ScrapingError):
    """The input file is missing or unreadable."""


class RetryableError(ScrapingError):
    """A transient failure worth retrying (timeouts, 429, 5xx, bot challenges)."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class LoaderError(ScrapingError):
    """A page could not be loaded (after retries, or a non-retryable HTTP error)."""

    def __init__(self, message: str, *, url: str, status_code: int | None = None, attempts: int = 1) -> None:
        super().__init__(message)
        self.url = url
        self.status_code = status_code
        self.attempts = attempts


class TransientLoaderError(RetryableError):
    """One failed load attempt that may succeed if repeated."""

    def __init__(self, message: str, *, status_code: int | None = None, retry_after: float | None = None) -> None:
        super().__init__(message, retry_after=retry_after)
        self.status_code = status_code


class BlockedError(RetryableError):
    """The target served a bot challenge / CAPTCHA instead of content."""


class ParseError(ScrapingError):
    """A page did not have the structure a parser expected."""


class WriterError(ScrapingError):
    """A record could not be validated or written."""
