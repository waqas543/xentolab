"""Small pure helpers shared by every stage: text, URLs, domains and stable IDs."""

from __future__ import annotations

import re
import secrets
import unicodedata
import uuid
from collections.abc import Hashable, Iterable
from datetime import UTC, datetime
from functools import lru_cache
from typing import TypeVar
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import tldextract

T = TypeVar("T", bound=Hashable)

_SPACE = re.compile(r"\s+")
_ID_NAMESPACE = uuid.UUID("6f1c5d0e-6a52-4a8e-9a39-0c7f4f8e2b11")
_TRACKING_PARAMS = re.compile(r"^(utm_\w+|gclid|fbclid|msclkid|mc_cid|mc_eid|_ga|ref|ref_src)$", re.I)


def utcnow() -> datetime:
    return datetime.now(UTC)


def normalize_whitespace(text: str | None) -> str:
    """Collapse all whitespace runs (incl. NBSP and newlines) into single spaces and trim."""
    if not text:
        return ""
    return _SPACE.sub(" ", unicodedata.normalize("NFKC", text)).strip()


def truncate(text: str | None, limit: int) -> str | None:
    if text is None:
        return None
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def unique(items: Iterable[T]) -> list[T]:
    """De-duplicate while keeping first-seen order."""
    return list(dict.fromkeys(items))


def stable_id(*parts: object) -> str:
    """Deterministic UUID for a record, so re-running a job yields the same IDs."""
    key = "|".join("" if part is None else str(part) for part in parts)
    return str(uuid.uuid5(_ID_NAMESPACE, key))


def new_job_id(job_name: str) -> str:
    return f"{job_name}-{utcnow():%Y%m%dT%H%M%SZ}-{secrets.token_hex(3)}"


def normalize_url(url: str) -> str:
    """Canonical form used for de-duplication: lowercase host, no fragment, tracking params or default port."""
    parts = urlsplit(url.strip())
    scheme = (parts.scheme or "http").lower()
    host = (parts.hostname or "").lower().rstrip(".")
    port = parts.port
    netloc = host if port is None or (scheme, port) in (("http", 80), ("https", 443)) else f"{host}:{port}"
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                       if not _TRACKING_PARAMS.match(k)])
    path = parts.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    return urlunsplit((scheme, netloc, path, query, ""))


def host_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower().removeprefix("www.")
    except ValueError:
        return ""


@lru_cache(maxsize=1)
def _extractor() -> tldextract.TLDExtract:
    # Bundled public-suffix snapshot only: no network access, no cache directory.
    return tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)


@lru_cache(maxsize=50_000)
def registrable_domain(url_or_host: str) -> str:
    """`https://shop.engro.com.pk/x` -> `engro.com.pk`. Falls back to the bare host for IPs/unknown TLDs."""
    host = host_of(url_or_host) if "/" in url_or_host else url_or_host.lower().removeprefix("www.")
    if not host:
        return ""
    parts = _extractor()(host)
    if parts.domain and parts.suffix:
        return f"{parts.domain}.{parts.suffix}"
    return host


@lru_cache(maxsize=50_000)
def domain_label(domain: str) -> str:
    """The name part of a registrable domain: `engro.com.pk` -> `engro`."""
    return _extractor()(domain).domain or domain.split(".")[0]


def domain_suffix(domain: str) -> str:
    return _extractor()(domain).suffix


def is_http_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.hostname)
