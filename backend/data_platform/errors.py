"""Error taxonomy. The engine decides retry behaviour from the error class, never from strings."""


class PlatformError(Exception):
    """Base class. `retryable` tells the stage runner whether another attempt can help."""

    retryable = False

    def __init__(self, message: str, *, context: dict | None = None):
        super().__init__(message)
        self.context = context or {}


class TransientError(PlatformError):
    """Network hiccups, timeouts, 5xx responses, locks: worth retrying with backoff."""

    retryable = True


class PermanentError(PlatformError):
    """Deterministic failures (bad input, schema mismatch, programming errors): never retried."""


class ValidationError(PermanentError):
    """An artifact or record set failed validation (checksum, format, schema)."""


class ParameterError(PermanentError):
    """Pipeline parameters failed validation."""


class SchemaError(PermanentError):
    """A provider schema changed in a way the parser cannot handle."""


class QualityGateError(PermanentError):
    """A data quality check with severity=error exceeded its threshold."""


class InsufficientDiskError(PermanentError):
    """Not enough free disk to proceed safely."""


class InvalidTransition(PlatformError):
    """A state-machine transition that is not allowed."""


class RunCancelled(Exception):
    """Raised inside a stage when cancellation was requested. Not an error."""


def is_retryable(error: BaseException) -> bool:
    if isinstance(error, PlatformError):
        return error.retryable
    # Unknown network-ish exceptions from libraries count as transient.
    transient_names = {"ConnectionError", "Timeout", "ReadTimeout", "ConnectTimeout", "ChunkedEncodingError",
                       "TimeoutError", "ConnectionResetError", "IncompleteRead", "OperationalError"}
    return type(error).__name__ in transient_names or isinstance(error, (TimeoutError, ConnectionError))
