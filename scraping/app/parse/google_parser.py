"""Google result-page parser: organic results and the AI Overview.

Organic results are the `#rso` blocks with an `a > h3` title. Google links them through
`/goto?url=<encrypted token>`, so the target URL is not in the page: the result keeps the absolute /goto link as
`redirect_url` (`resolved=False`) and, meanwhile, the site's origin from the breadcrumb (`https://site.pk › …`;
breadcrumb segments are labels, not paths). GoogleEngine resolves the redirect with one plain request. Direct
and `/url?q=` links are used as they are.

The AI Overview is the text of `#m-x-content [data-container-id=main-col]` without its citation chips, as plain
text with "- " bullets. Its sources are the cards Google embeds as JSON in `<!--TgQPHd|||[...]-->` comments,
matched to the chips by `data-icl-uuid`.

A captcha / "unusual traffic" page raises BlockedError. A page with no results, no overview and no "did not
match any documents" marker raises ParseError (the layout changed or the page is not a result page).
"""

from __future__ import annotations

import html as html_lib
import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urljoin, urlsplit

from selectolax.lexbor import LexborHTMLParser, LexborNode

from app.core.exceptions import BlockedError, ParseError
from app.core.models import PageResponse, SearchPage, SearchResult, SearchSummary, SummarySource
from app.core.utils import host_of, is_http_url, normalize_whitespace, registrable_domain, unique
from app.parse.base_parser import BaseParser, register_parser
from app.parse.html_parser import HtmlDocument, node_text

ENGINE = "google"
_BLOCK_MARKERS = re.compile(r"unusual traffic from your computer|g-recaptcha|id=\"captcha-form\"|/sorry/index|"
                            r"detected unusual traffic", re.I)
_NO_RESULTS = re.compile(r"did not match any documents|No results found for|Your search did not match", re.I)
_EMBEDDED = re.compile(r"<!--TgQPHd\|\|\|(.*?)-->", re.S)
_READ_MORE = re.compile(r"\s*(?:Read more|More)\s*$")
_FOLLOW_UP = re.compile(r"^(?:If you(?:'d| would) like|Would you like|Do you want)\b", re.I)
_BLOCK_TAGS = {"div", "p", "ul", "ol", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "tr", "section", "br",
               "blockquote"}
_SKIP_TAGS = {"style", "script", "noscript", "svg", "button", "template"}
_MIN_OVERVIEW_CHARS = 40


def google_link(href: str, page_url: str) -> tuple[str, bool] | None:
    """(url, direct) for a result link: direct links and `/url?q=` unwrap; `/goto?url=<token>` stays a redirect
    (direct=False). Other Google-internal links (searches, images) return None."""
    href = (href or "").strip()
    if not href or href.startswith("#"):
        return None
    absolute = urljoin(page_url, href)
    parts = urlsplit(absolute)
    host = (parts.hostname or "").lower()
    is_google = host == "google.com" or host.startswith("www.google.")
    if is_google and parts.path == "/url":
        params = parse_qs(parts.query)
        target = (params.get("q") or params.get("url") or [""])[0]
        return (target, True) if is_http_url(target) else None
    if is_google and parts.path == "/goto":
        return absolute, False
    if is_google or not is_http_url(absolute):
        return None
    return absolute, True


def breadcrumb_origin(text: str) -> str | None:
    """`https://digikhata.pk › about_us` -> `https://digikhata.pk/` (the labels after › are not URL paths)."""
    base = normalize_whitespace(text).split("›")[0].strip()
    if not base.startswith(("http://", "https://")) or not is_http_url(base):
        return None
    parts = urlsplit(base)
    return f"{parts.scheme}://{parts.netloc}/"


@dataclass
class _Card:
    uuid: str
    title: str | None
    snippet: str | None
    site: str | None
    site_names: list[str]
    redirect_url: str | None
    url: str | None = None


@register_parser
class GoogleSearchParser(BaseParser[SearchPage]):
    name = "google_result_parser"

    def parse(self, page: PageResponse) -> SearchPage:
        doc = HtmlDocument(page.html, page.final_url)
        results = self._results(doc, page.final_url)
        overview = self._ai_overview(doc, page.final_url)
        if not results and overview is None:
            if _BLOCK_MARKERS.search(page.html):
                raise BlockedError("Google served a captcha (unusual traffic)")
            if _NO_RESULTS.search(page.html):
                return SearchPage()
            raise ParseError("no Google results or AI Overview and no 'no results' marker: layout changed or bad page")
        return SearchPage(results=results, summaries=[overview] if overview else [])

    # ------------------------------------------------------------ organic results

    def _results(self, doc: HtmlDocument, page_url: str) -> list[SearchResult]:
        results: list[SearchResult] = []
        seen: set[str] = set()
        for heading in doc.css("#rso a h3"):
            anchor = _closest(heading, "a")
            link = google_link(anchor.attributes.get("href") or "", page_url) if anchor else None
            if anchor is None or link is None or link[0] in seen:
                continue
            seen.add(link[0])
            href, direct = link
            box = _result_box(anchor)
            crumb = next((t for c in box.css("cite") if (t := normalize_whitespace(c.text(separator="")))
                          .startswith("http")), None)
            snippet_node = box.css_first(".VwiC3b") or box.css_first("[data-sncf]")
            snippet = _READ_MORE.sub("", node_text(snippet_node)) or None
            site_name = node_text(box.css_first(".VuuXrf")) or None
            url = href if direct else (breadcrumb_origin(crumb or "") or href)
            results.append(SearchResult(rank=len(results) + 1, title=node_text(heading) or url, url=url,
                                        domain=host_of(url), display_url=crumb, snippet=snippet, engine=ENGINE,
                                        site_name=site_name, redirect_url=None if direct else href,
                                        resolved=direct))
        return results

    # ------------------------------------------------------------ AI Overview

    def _ai_overview(self, doc: HtmlDocument, page_url: str) -> SearchSummary | None:
        container = doc.css_first("#m-x-content")
        if container is None:
            return None
        main = container.css_first('[data-container-id="main-col"]') or container
        tree = LexborHTMLParser(main.html or "")
        chips = unique(uuid for node in tree.css("[data-icl-uuid]") if (uuid := node.attributes.get("data-icl-uuid")))
        text = _overview_text(tree)
        if len(text) < _MIN_OVERVIEW_CHARS:
            return None
        return SearchSummary(engine=ENGINE, kind="ai_overview", text=text,
                             sources=_overview_sources(doc.html, chips, page_url), page_url=page_url)


# ---------------------------------------------------------------- helpers


def _closest(node: LexborNode, tag: str) -> LexborNode | None:
    while node is not None and node.tag != tag:
        node = node.parent
    return node


def _result_box(anchor: LexborNode) -> LexborNode:
    """The largest ancestor of a result link that holds no other result title."""
    box = anchor
    while (parent := box.parent) is not None and parent.attributes.get("id") != "rso" \
            and len(parent.css("a h3")) <= 1:
        box = parent
    return box


def _overview_text(tree: LexborHTMLParser) -> str:
    """Block-aware text: one line per paragraph/heading, "- " bullets (indented when nested), no chips."""
    lines: list[str] = []
    current: list[str] = []
    prefix = [""]

    def flush() -> None:
        text = normalize_whitespace("".join(current))
        if text:
            lines.append(prefix[0] + text)
        current.clear()
        prefix[0] = ""

    def walk(node: LexborNode, depth: int) -> None:
        for child in node.iter(include_text=True):
            tag = child.tag
            if tag == "-text":
                current.append(child.text_content or "")
            elif tag in _SKIP_TAGS or child.attributes.get("data-icl-uuid"):
                continue
            elif tag == "li":
                flush()
                prefix[0] = "  " * depth + "- "
                walk(child, depth + 1)
                flush()
            elif tag in _BLOCK_TAGS:
                flush()
                walk(child, depth)
                flush()
            else:
                walk(child, depth)

    root = tree.body or tree.root
    if root is not None:
        walk(root, 0)
    flush()
    for index, line in enumerate(lines):
        if _FOLLOW_UP.match(line.lstrip("- ")):
            lines = lines[:index]
            break
    return "\n".join(lines).strip()


def _overview_sources(html: str, chips: list[str], page_url: str) -> list[SummarySource]:
    """Source cards for the chips (all cards when no chip matched), in chip order and de-duplicated. A chip's
    plain citation link fills in the URL of its card on the same site, or becomes a source of its own."""
    cards: list[_Card] = []
    direct: list[tuple[str, str]] = []
    for raw in _EMBEDDED.findall(html):
        try:
            data = json.loads(html_lib.unescape(raw))
        except ValueError:
            continue
        for item in data if isinstance(data, list) else []:
            if card := _card(item, page_url):
                cards.append(card)
            elif link := _direct_link(item):
                direct.append(link)
    if chips:
        order = {uuid: i for i, uuid in enumerate(chips)}
        cards = sorted((c for c in cards if c.uuid in order), key=lambda c: order[c.uuid])
        direct = [link for link in direct if link[0] in order]
    unique_cards: list[_Card] = []
    seen: set[str] = set()
    for card in cards:
        key = card.redirect_url or card.title or ""
        if key not in seen:
            seen.add(key)
            unique_cards.append(card)

    extra: list[SummarySource] = []
    for uuid, url in unique(direct):
        host = registrable_domain(url)
        match = next((c for c in unique_cards if c.uuid == uuid and c.url is None and c.site
                      and registrable_domain(c.site) == host), None)
        if match is not None:
            match.url = url
        elif all(c.url != url for c in unique_cards) and all(s.url != url for s in extra):
            parts = urlsplit(url)
            extra.append(SummarySource(url=url, site=f"{parts.scheme}://{parts.netloc}"))
    return [SummarySource(title=c.title, url=c.url, site=c.site, site_name=" · ".join(c.site_names) or None,
                          snippet=c.snippet, redirect_url=c.redirect_url) for c in unique_cards] + extra


def _card(item: Any, page_url: str) -> _Card | None:
    """`[uuid, [title, snippet, favicon, origin, [site names], "/goto?url=...", ...], ...]`."""
    if not (isinstance(item, list) and len(item) > 1 and isinstance(item[0], str) and isinstance(item[1], list)):
        return None
    body = item[1]
    if len(body) < 6 or not isinstance(body[0], str) or not isinstance(body[5], str) \
            or not body[5].startswith("/goto"):
        return None
    site = body[3] if isinstance(body[3], str) and is_http_url(body[3]) else None
    names = [n for n in body[4] if isinstance(n, str)] if isinstance(body[4], list) else []
    snippet = body[1] if isinstance(body[1], str) else None
    return _Card(uuid=item[0], title=normalize_whitespace(body[0]) or None,
                 snippet=_READ_MORE.sub("", normalize_whitespace(snippet)) or None, site=site, site_names=names,
                 redirect_url=urljoin(page_url, body[5]))


def _direct_link(item: Any) -> tuple[str, str] | None:
    """Citation chips with a plain link: `[uuid, null, null, 0, favicon, "Apple", 2, "https://...", ...]`."""
    if not (isinstance(item, list) and len(item) > 7 and isinstance(item[0], str) and item[1] is None):
        return None
    url = item[7]
    return (item[0], url) if isinstance(url, str) and is_http_url(url) else None
