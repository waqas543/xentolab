"""Writer plugin interface and registry. Writers receive already validated, JSON-ready dicts and know nothing
about jobs: the OutputManager routes each record by its `record_type`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar, TypeVar

from app.core.exceptions import ConfigError


class BaseWriter(ABC):
    name: ClassVar[str]

    def __init__(self, output_dir: Path, prefix_for: Callable[[str], str], max_records_per_file: int,
                 subdir_for: Callable[[str], str] | None = None) -> None:
        self.output_dir = output_dir
        self.prefix_for = prefix_for
        self.max_records_per_file = max_records_per_file
        self.subdir_for = subdir_for or (lambda record_type: "")

    def directory_for(self, record_type: str) -> Path:
        """Where a record type's files go: output_dir, or a folder inside it (e.g. output/data/linkedin)."""
        return self.output_dir / self.subdir_for(record_type)

    @abstractmethod
    def write(self, record_type: str, data: dict[str, Any]) -> None:
        """Persist one record."""

    @abstractmethod
    def flush(self) -> None:
        """Make everything written so far durable."""

    @abstractmethod
    def close(self) -> None:
        """Flush and release resources."""

    def files(self) -> list[Path]:
        """Files this writer produced or appended to."""
        return []


WRITERS: dict[str, type[BaseWriter]] = {}
W = TypeVar("W", bound=type[BaseWriter])


def register_writer(cls: W) -> W:
    WRITERS[cls.name] = cls
    return cls


def writer_class(name: str) -> type[BaseWriter]:
    if name not in WRITERS:
        raise ConfigError(f"unknown writer {name!r}; available: {', '.join(sorted(WRITERS))}")
    return WRITERS[name]
