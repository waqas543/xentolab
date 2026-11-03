"""DuckDuckGo result-page parser. Understands the three layouts DDG serves:

- html.duckduckgo.com/html  (div.result, a.result__a, .result__snippet)       <- default search URL
- duckduckgo.com (JS)       (article[data-testid=result], data-testid/data-result attributes)
- lite.duckduckgo.com       (a.result-link, td.result-snippet)

Ads are dropped, DDG redirect links (/l/?uddg=...) are unwrapped, and bot-challenge pages raise BlockedError
so the company is retried later instead of being recorded as "no results".
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, unquote, urlsplit

from app.core.exceptions import BlockedError, ParseError
from app.core.models import PageResponse, SearchResult
from app.core.utils import host_of, is_http_url, normalize_url, normalize_whitespace
from app.parse.base_parser import BaseParser, register_parser
from app.parse.html_parser import HtmlDocument, node_text

_BLOCK_MARKERS = re.compile(r"anomaly-modal|anomaly\.js|bots use DuckDuckGo too|challenge-form|"
                            r"Please complete the following challenge", re.I)
_NO_RESULTS = re.compile(r"class=\"[^\"]*no-results|No results found for|No more results", re.I)
_AD_HOSTS = ("duckduckgo.com/y.js", "bing.com/aclick", "ad_provider=", "ad_domain=")


def unwrap_ddg_url(href: str) -> str:
    """`//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com&rut=...` -> `https://example.com`."""
    href = href.strip()
    if href.startswith("//"):
        href = "https:" + href
    parts = urlsplit(href)
    if parts.hostname and parts.hostname.endswith("duckduckgo.com") and parts.path.startswith("/l/"):
        target = parse_qs(parts.query).get("uddg")
        if target:
            return unquote(target[0])
    return href


def _is_ad(href: str, classes: str = "") -> bool:
    return "result--ad" in classes or any(marker in href for marker in _AD_HOSTS)


@register_parser
class SearchResultParser(BaseParser[list[SearchResult]]):
    name = "search_result_parser"

    def parse(self, page: PageResponse) -> list[SearchResult]:
        doc = HtmlDocument(page.html, page.final_url)
        raw = self._html_layout(doc) or self._js_layout(doc) or self._lite_layout(doc)
        if not raw:
            if _BLOCK_MARKERS.search(page.html):
                raise BlockedError("DuckDuckGo served a bot challenge")
            if _NO_RESULTS.search(page.html):
                return []
            raise ParseError("no DuckDuckGo results found and no 'no results' marker: layout changed or bad page")

        results: list[SearchResult] = []
        seen: set[str] = set()
        for title, href, display, snippet in raw:
            url = unwrap_ddg_url(href)
            if not is_http_url(url) or _is_ad(url):
                continue
            key = normalize_url(url)
            if key in seen:
                continue
            seen.add(key)
            results.append(SearchResult(rank=len(results) + 1, title=title or url, url=url, domain=host_of(url),
                                        display_url=display or None, snippet=snippet or None))
        return results

    @staticmethod
    def _html_layout(doc: HtmlDocument) -> list[tuple[str, str, str, str]]:
        found = []
        for node in doc.css("div.result"):
            link = node.css_first("a.result__a")
            if link is None:
                continue
            href = link.attributes.get("href") or ""
            if _is_ad(href, node.attributes.get("class") or ""):
                continue
            found.append((node_text(link), href, node_text(node.css_first(".result__url")),
                          node_text(node.css_first(".result__snippet"))))
        return found

    @staticmethod
    def _js_layout(doc: HtmlDocument) -> list[tuple[str, str, str, str]]:
        found = []
        for node in doc.css('article[data-testid="result"]'):
            link = node.css_first('a[data-testid="result-title-a"]') or node.css_first("h2 a")
            if link is None:
                continue
            href = link.attributes.get("href") or ""
            if _is_ad(href) or node.attributes.get("data-nrn") == "ad":
                continue
            display = node.css_first('a[data-testid="result-extras-url-link"]')
            snippet = node.css_first('[data-result="snippet"]')
            found.append((node_text(link), href, node_text(display), node_text(snippet)))
        return found

    @staticmethod
    def _lite_layout(doc: HtmlDocument) -> list[tuple[str, str, str, str]]:
        links = doc.css("a.result-link")
        snippets = [normalize_whitespace(n.text()) for n in doc.css("td.result-snippet")]
        displays = [normalize_whitespace(n.text()) for n in doc.css("span.link-text")]
        found = []
        for index, link in enumerate(links):
            href = link.attributes.get("href") or ""
            if _is_ad(href):
                continue
            found.append((node_text(link), href, displays[index] if index < len(displays) else "",
                          snippets[index] if index < len(snippets) else ""))
        return found
