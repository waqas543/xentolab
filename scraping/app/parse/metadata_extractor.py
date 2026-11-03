"""Page metadata: title, meta description, canonical URL, language, keywords, Open Graph, Twitter cards and
JSON-LD structured data.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

import orjson

from app.core.models import PageMetadata, PageResponse
from app.core.utils import normalize_whitespace, truncate, unique
from app.parse.base_parser import BaseParser, register_parser
from app.parse.html_parser import HtmlDocument

MAX_JSON_LD_BLOCKS = 20
MAX_JSON_LD_BYTES = 100_000
_ORG_TYPES = ("Organization", "Corporation", "LocalBusiness", "Store", "Company")


def _site_name(meta: dict[str, str], json_ld: list[dict[str, Any]]) -> str | None:
    if meta.get("og:site_name"):
        return meta["og:site_name"]
    for block in json_ld:
        types = block.get("@type")
        types = types if isinstance(types, list) else [types]
        if any(isinstance(t, str) and t.endswith(_ORG_TYPES) for t in types) and isinstance(block.get("name"), str):
            return normalize_whitespace(block["name"]) or None
    return None


def _bounded_json_ld(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    kept, size = [], 0
    for block in blocks[:MAX_JSON_LD_BLOCKS]:
        try:
            block_size = len(orjson.dumps(block, default=str))
        except TypeError:
            continue
        if size + block_size > MAX_JSON_LD_BYTES:
            break
        kept.append(block)
        size += block_size
    return kept


def extract_metadata(doc: HtmlDocument) -> PageMetadata:
    meta = doc.meta
    canonical = doc.css_first('link[rel="canonical"]')
    canonical_url = canonical.attributes.get("href") if canonical is not None else None
    keywords = unique(k.strip() for k in (meta.get("keywords") or "").split(",") if k.strip())[:30]
    json_ld = _bounded_json_ld(doc.json_ld)
    return PageMetadata(
        url=doc.url,
        title=truncate(doc.title, 500),
        description=truncate(meta.get("description") or meta.get("og:description"), 2000),
        canonical_url=urljoin(doc.url, canonical_url) if canonical_url else None,
        language=doc.language,
        keywords=keywords,
        site_name=_site_name(meta, json_ld),
        open_graph={k.removeprefix("og:"): v for k, v in meta.items() if k.startswith("og:")},
        twitter={k.removeprefix("twitter:"): v for k, v in meta.items() if k.startswith("twitter:")},
        json_ld=json_ld,
    )


@register_parser
class MetadataExtractor(BaseParser[PageMetadata]):
    name = "metadata_extractor"

    def parse(self, page: PageResponse) -> PageMetadata:
        return self.extract(HtmlDocument(page.html, page.final_url))

    def extract(self, doc: HtmlDocument) -> PageMetadata:
        return extract_metadata(doc)
