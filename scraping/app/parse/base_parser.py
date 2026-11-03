"""Parser plugin interface and registry.

A parser turns one PageResponse into structured data. Register it with `@register_parser` and list its `name`
in a job's `parsers:`; or reference any importable class as `package.module:ClassName` without registering.
"""

from __future__ import annotations

import importlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar, Generic, TypeVar

from app.core.config import ExtractionRules, JobConfig
from app.core.exceptions import ConfigError
from app.core.models import PageResponse

T = TypeVar("T")


@dataclass
class ParseContext:
    rules: ExtractionRules
    config: JobConfig | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class BaseParser(ABC, Generic[T]):
    name: ClassVar[str]

    def __init__(self, context: ParseContext) -> None:
        self.context = context

    @property
    def rules(self) -> ExtractionRules:
        return self.context.rules

    @abstractmethod
    def parse(self, page: PageResponse) -> T:
        """Extract structured data from one page. Must be pure: no I/O, no shared mutable state."""


PARSERS: dict[str, type[BaseParser[Any]]] = {}
P = TypeVar("P", bound=type[BaseParser[Any]])


def register_parser(cls: P) -> P:
    if cls.name in PARSERS and PARSERS[cls.name] is not cls:
        raise ConfigError(f"parser name {cls.name!r} registered twice")
    PARSERS[cls.name] = cls
    return cls


def parser_class(name: str) -> type[BaseParser[Any]]:
    if ":" in name:
        module_name, _, attr = name.partition(":")
        try:
            cls = getattr(importlib.import_module(module_name), attr)
        except (ImportError, AttributeError) as exc:
            raise ConfigError(f"cannot import parser {name!r}: {exc}") from exc
        if not (isinstance(cls, type) and issubclass(cls, BaseParser)):
            raise ConfigError(f"{name!r} is not a BaseParser subclass")
        return cls
    if name not in PARSERS:
        raise ConfigError(f"unknown parser {name!r}; available: {', '.join(sorted(PARSERS))}")
    return PARSERS[name]


def create_parsers(names: list[str], context: ParseContext) -> dict[str, BaseParser[Any]]:
    """Instantiate the job's parsers, keyed by the name used in the config."""
    return {name: parser_class(name)(context) for name in names}
