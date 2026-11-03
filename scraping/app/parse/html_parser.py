"""HtmlDocument: a thin, cached wrapper over selectolax (lexbor) used by every HTML parser.

Also registers `html_parser`, a generic parser returning title, visible text and links for crawl jobs.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import cached_property
from typing import Any
from urllib.parse import urljoin, urlsplit

from selectolax.lexbor import LexborHTMLParser, LexborNode

from app.core.models import PageResponse
from app.core.utils import normalize_whitespace, truncate
from app.parse.base_parser import BaseParser, register_parser

_BR = re.compile(r"<br\s*/?>", re.I)
_LINE_SPACE = re.compile(r"[ \t\r\f\v ]+")
_BLANK_LINES = re.compile(r"\n\s*\n+")
_NON_CONTENT = ["script", "style", "noscript", "svg", "template", "iframe", "head"]


@dataclass(frozen=True)
class Link:
    url: str
    text: str


class HtmlDocument:
    def __init__(self, html: str, url: str) -> None:
        self.html = html
        self.url = url
        self.tree = LexborHTMLParser(html)

    def css(self, selector: str) -> list[LexborNode]:
        return self.tree.css(selector)

    def css_first(self, selector: str) -> LexborNode | None:
        return self.tree.css_first(selector)

    @cached_property
    def title(self) -> str | None:
        node = self.tree.css_first("title")
        return normalize_whitespace(node.text()) or None if node else None

    @cached_property
    def language(self) -> str | None:
        node = self.tree.css_first("html")
        return (node.attributes.get("lang") or "").strip()[:20] or None if node else None

    @cached_property
    def meta(self) -> dict[str, str]:
        """First value of every <meta name|property=... content=...>, keys lowercased."""
        values: dict[str, str] = {}
        for node in self.tree.css("meta[content]"):
            key = (node.attributes.get("property") or node.attributes.get("name") or "").strip().lower()
            content = normalize_whitespace(node.attributes.get("content"))
            if key and content and key not in values:
                values[key] = content
        return values

    @cached_property
    def links(self) -> list[Link]:
        """Absolute http(s)/mailto/tel links in document order, de-duplicated."""
        seen: dict[str, Link] = {}
        for node in self.tree.css("a[href]"):
            href = (node.attributes.get("href") or "").strip()
            if not href or href.startswith(("#", "javascript:")):
                continue
            try:
                url = urljoin(self.url, href)
                scheme = urlsplit(url).scheme
            except ValueError:
                continue
            if scheme in ("http", "https", "mailto", "tel") and url not in seen:
                seen[url] = Link(url=url, text=normalize_whitespace(node.text())[:200])
        return list(seen.values())

    @cached_property
    def json_ld(self) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        for node in self.tree.css('script[type="application/ld+json"]'):
            raw = (node.text() or "").strip().rstrip(";")
            if not raw:
                continue
            try:
                data = json.loads(raw, strict=False)
            except ValueError:
                continue
            items = data if isinstance(data, list) else [data]
            blocks.extend(item for item in items if isinstance(item, dict))
        return blocks

    @cached_property
    def text(self) -> str:
        """Visible text, one block per line (<br> becomes ', ' so multi-line addresses stay together)."""
        tree = LexborHTMLParser(_BR.sub(", ", self.html))
        tree.strip_tags(_NON_CONTENT)
        body = tree.body or tree.root
        if body is None:
            return ""
        raw = body.text(separator="\n")
        lines = (_LINE_SPACE.sub(" ", line).strip(" ,") for line in raw.split("\n"))
        return _BLANK_LINES.sub("\n", "\n".join(line for line in lines if line)).strip()


def node_text(node: LexborNode | None) -> str:
    return normalize_whitespace(node.text()) if node is not None else ""


@register_parser
class GenericHtmlParser(BaseParser[dict[str, Any]]):
    """Title, visible text (truncated) and outgoing links: a starting point for custom crawl jobs."""

    name = "html_parser"
    text_limit = 20_000

    def parse(self, page: PageResponse) -> dict[str, Any]:
        doc = HtmlDocument(page.html, page.final_url)
        return {"title": doc.title, "text": truncate(doc.text, self.text_limit),
                "links": [link.url for link in doc.links if link.url.startswith("http")]}
