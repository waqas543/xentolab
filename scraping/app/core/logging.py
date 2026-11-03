"""Structured logging: `log.info("page.loaded", url=..., latency_ms=...)`.

Console output goes through rich as `event  key=value ...`; an optional file handler writes one JSON object
per line so logs can be grepped or loaded later.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import orjson
from rich.console import Console
from rich.logging import RichHandler

_FIELDS = "fields"
_configured = False


class StructuredLogger:
    def __init__(self, name: str) -> None:
        self._logger = logging.getLogger(name)

    def _log(self, level: int, event: str, fields: dict[str, Any]) -> None:
        if self._logger.isEnabledFor(level):
            self._logger.log(level, event, extra={_FIELDS: fields}, stacklevel=3)

    def debug(self, event: str, **fields: Any) -> None:
        self._log(logging.DEBUG, event, fields)

    def info(self, event: str, **fields: Any) -> None:
        self._log(logging.INFO, event, fields)

    def warning(self, event: str, **fields: Any) -> None:
        self._log(logging.WARNING, event, fields)

    def error(self, event: str, **fields: Any) -> None:
        self._log(logging.ERROR, event, fields)


def _fields(record: logging.LogRecord) -> dict[str, Any]:
    return getattr(record, _FIELDS, None) or {}


class ConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        pairs = " ".join(f"{key}={_short(value)}" for key, value in _fields(record).items() if value is not None)
        return f"{record.getMessage()}  {pairs}" if pairs else record.getMessage()


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {"ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"), "level": record.levelname,
                   "logger": record.name, "event": record.getMessage(), **_fields(record)}
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return orjson.dumps(payload, default=str).decode()


def _short(value: Any, limit: int = 160) -> str:
    text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def setup_logging(level: str = "INFO", log_file: Path | None = None, console: Console | None = None) -> None:
    global _configured
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root.setLevel(level.upper())

    rich_handler = RichHandler(console=console or Console(stderr=True), show_path=False, markup=False,
                               rich_tracebacks=True, log_time_format="%H:%M:%S")
    rich_handler.setFormatter(ConsoleFormatter())
    root.addHandler(rich_handler)

    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(JsonFormatter())
        root.addHandler(file_handler)

    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _configured = True


def get_logger(name: str) -> StructuredLogger:
    return StructuredLogger(name)
