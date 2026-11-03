"""Message contracts between the agents.

Messages are plain JSON so any agent can be rewritten in another language: `python -m data_platform.scrape
schemas` prints the JSON Schema of each one. Every message carries `v`; a consumer rejects versions it does
not know (the message goes to the dead-letter stream instead of being half-understood).
"""

from __future__ import annotations

import hashlib
import json
import types
import typing
from dataclasses import MISSING, asdict, dataclass, field, fields
from functools import cache
from typing import ClassVar

import jsonschema

from ..errors import PermanentError

SCHEMA_VERSION = 1

# Stream names. One consumer group per stream, named after the agent that consumes it.
REQUESTS = "scrape:requests"
FETCHED = "scrape:fetched"
PARSED = "scrape:parsed"
ENRICH_REQUESTS = "enrich:requests"
ENRICH_RESULTS = "enrich:results"
DLQ = "scrape:dlq"
RETRY = "scrape:retry"            # sorted set of FetchRequests, scored by due time (epoch seconds)


def request_id(campaign_id: int, url: str) -> str:
    """Stable per (campaign, url): redelivered or retried messages keep their id, so writes stay idempotent."""
    return hashlib.sha256(f"{campaign_id}:{url}".encode()).hexdigest()[:32]


class Message:
    stream: ClassVar[str]

    def to_json(self) -> str:
        return json.dumps({"v": SCHEMA_VERSION, **asdict(self)}, ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: str | bytes):
        try:
            data = json.loads(raw)
        except ValueError as error:
            raise PermanentError(f"{cls.__name__}: not JSON: {error}") from None
        if data.get("v") != SCHEMA_VERSION:
            raise PermanentError(f"{cls.__name__}: unsupported schema version {data.get('v')!r}")
        try:
            jsonschema.validate(data, json_schema(cls))
        except jsonschema.ValidationError as error:
            raise PermanentError(f"{cls.__name__}: {error.message}") from None
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in names})


@dataclass
class FetchRequest(Message):
    stream: ClassVar[str] = REQUESTS
    request_id: str
    campaign_id: int
    domain: str
    country: str
    url: str
    kind: str                       # home | about | contact | team | company
    depth: int
    attempt: int = 1
    site: str = "default"           # site plugin (data_platform/scrape/sites) that crawls and parses it


@dataclass
class FetchResult(Message):
    """What the fetcher saw. The body is in the blob store under `blob_key`, never in the message."""

    stream: ClassVar[str] = FETCHED
    request_id: str
    campaign_id: int
    domain: str
    country: str
    url: str
    kind: str
    depth: int
    attempt: int
    fetched_at: str
    final_url: str | None = None
    status: int | None = None
    content_type: str | None = None
    bytes: int | None = None
    blob_key: str | None = None
    sha256: str | None = None
    proxy_id: str | None = None
    elapsed_ms: int | None = None
    site: str = "default"
    error_class: str | None = None  # timeout | dns | connect | network | tls | proxy | geo_block | http_4xx | http_5xx
    error_message: str | None = None  # | too_large | not_html | robots_denied | redirect_loop | other


@dataclass
class ParsedPage(Message):
    """Parser output for one fetch. Failed fetches pass through with `error_class` set and nothing extracted."""

    stream: ClassVar[str] = PARSED
    request_id: str
    campaign_id: int
    domain: str
    country: str
    url: str
    kind: str
    depth: int
    attempt: int
    fetched_at: str
    parser_version: str
    final_url: str | None = None
    status: int | None = None
    blob_key: str | None = None
    sha256: str | None = None
    bytes: int | None = None
    title: str | None = None
    lang: str | None = None
    text: str | None = None
    # [{"field", "value", "source", "evidence"?, "confidence"?}]
    facts: list[dict] = field(default_factory=list)
    # same-site pages worth fetching next: [{"url", "kind", "priority"}]
    links: list[dict] = field(default_factory=list)
    site: str = "default"
    error_class: str | None = None
    error_message: str | None = None


@dataclass
class EnrichRequest(Message):
    stream: ClassVar[str] = ENRICH_REQUESTS
    campaign_id: int
    domain: str
    country: str


@dataclass
class EnrichResult(Message):
    stream: ClassVar[str] = ENRICH_RESULTS
    campaign_id: int
    domain: str
    country: str
    model: str
    prompt_version: str
    facts: list[dict] = field(default_factory=list)
    error_class: str | None = None
    error_message: str | None = None


MESSAGES: list[type[Message]] = [FetchRequest, FetchResult, ParsedPage, EnrichRequest, EnrichResult]

_JSON_TYPES = {str: "string", int: "integer", float: "number", bool: "boolean", list: "array", dict: "object"}


@cache
def json_schema(cls: type[Message]) -> dict:
    hints = typing.get_type_hints(cls)
    properties: dict[str, dict] = {"v": {"const": SCHEMA_VERSION}}
    required = ["v"]
    for f in fields(cls):
        hint = hints[f.name]
        args = typing.get_args(hint) if isinstance(hint, types.UnionType) else (hint,)
        names = [_JSON_TYPES.get(typing.get_origin(a) or a, "null") for a in args]
        properties[f.name] = {"type": names[0] if len(names) == 1 else names}
        if typing.get_origin(hint) is list:
            properties[f.name]["items"] = {"type": "object"}
        if f.default is MISSING and f.default_factory is MISSING:
            required.append(f.name)
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "title": cls.__name__, "type": "object",
            "properties": properties, "required": required}
