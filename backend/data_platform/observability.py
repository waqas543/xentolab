"""Structured logging with run context, secret redaction, and run-log persistence.

Every log line carries the active context (pipeline_run_id, stage, source, ...) via
contextvars, so code deep inside an adapter does not need to pass it around.
"""

import contextvars
import json
import logging
import re
import socket
import os
import sys
import time
from contextlib import contextmanager

_context: contextvars.ContextVar[dict] = contextvars.ContextVar("dp_log_context", default={})

WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"

_SECRET_KEY = re.compile(r"(pass(word)?|secret|token|api[_-]?key|authorization|credential)", re.I)
_URL_CREDENTIALS = re.compile(r"(?P<scheme>[a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@", re.I)
_QUERY_SECRET = re.compile(r"((?:api[_-]?key|token|access_token|secret|password)=)[^&\s]+", re.I)


def redact(value):
    """Mask secrets in strings, dicts and lists before they are logged or stored."""
    if isinstance(value, str):
        return _QUERY_SECRET.sub(r"\1***", _URL_CREDENTIALS.sub(r"\g<scheme>***@", value))
    if isinstance(value, dict):
        return {k: ("***" if _SECRET_KEY.search(str(k)) else redact(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


def current_context() -> dict:
    return dict(_context.get())


@contextmanager
def log_context(**values):
    token = _context.set({**_context.get(), **{k: v for k, v in values.items() if v is not None}})
    try:
        yield
    finally:
        _context.reset(token)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "message": redact(record.getMessage()),
            "worker": WORKER_ID,
            **current_context(),
        }
        extra = getattr(record, "fields", None)
        if extra:
            payload.update(redact(extra))
        if record.exc_info:
            payload["exception"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    if any(getattr(h, "_dp_handler", False) for h in root.handlers):
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler._dp_handler = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(level)


def get_logger(name: str) -> logging.LoggerAdapter:
    return StructuredLogger(logging.getLogger(name), {})


class StructuredLogger(logging.LoggerAdapter):
    """logger.info("msg", rows=10) -> structured field `rows` on the record."""

    def process(self, msg, kwargs):
        fields = {k: kwargs.pop(k) for k in list(kwargs) if k not in ("exc_info", "stack_info", "stacklevel", "extra")}
        kwargs.setdefault("extra", {})["fields"] = fields
        return msg, kwargs


class RunLogHandler(logging.Handler):
    """Persists INFO+ log records emitted inside a run to dp.pipeline_logs (buffered)."""

    def __init__(self, write_batch, flush_every: int = 50, flush_seconds: float = 2.0):
        super().__init__(level=logging.INFO)
        self._write_batch = write_batch
        self._buffer: list[tuple] = []
        self._flush_every = flush_every
        self._flush_seconds = flush_seconds
        self._last_flush = time.monotonic()

    def emit(self, record: logging.LogRecord) -> None:
        context = current_context()
        run_id = context.get("pipeline_run_id")
        if run_id is None:
            return
        fields = redact(getattr(record, "fields", None) or {})
        message = redact(record.getMessage())
        if record.exc_info:
            message = f"{message}\n{redact(logging.Formatter().formatException(record.exc_info))}"
        self._buffer.append((run_id, context.get("stage"), record.levelname, message,
                             {k: v for k, v in {**context, **fields}.items() if k not in ("pipeline_run_id",)}))
        if len(self._buffer) >= self._flush_every or time.monotonic() - self._last_flush > self._flush_seconds:
            self.flush()

    def flush(self) -> None:
        if not self._buffer:
            return
        batch, self._buffer = self._buffer, []
        self._last_flush = time.monotonic()
        try:
            self._write_batch(batch)
        except Exception:  # logging must never break the pipeline; the stdout copy still exists
            sys.stderr.write("failed to persist run logs\n")
