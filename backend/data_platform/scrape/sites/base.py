"""The site plugin contract: per-company crawling and parsing rules on top of the generic engine.

A plugin is a Site subclass in data_platform/scrape/sites/ that lists the domains it handles. Every agent
resolves the same plugin from the `site` name carried in each message, so the agents stay stateless.
Override only what differs; the defaults are the generic crawler and parser.
"""

from __future__ import annotations

from selectolax.lexbor import LexborHTMLParser

from ..extract import follow_links


class Site:
    name = "default"
    version = "1"                       # bump when extraction changes; recorded on every parsed page
    domains: tuple[str, ...] = ()        # registrable domains this plugin handles, e.g. ("daraz.pk",)

    # Crawl budget and politeness overrides (None = engine/campaign default).
    max_pages: int | None = None
    max_depth: int | None = None
    host_delay_seconds: float | None = None
    headers: dict[str, str] = {}

    # -- crawling (frontier, fetcher) ------------------------------------------------------

    def seeds(self, domain: str) -> list[tuple[str, str]]:
        """Pages to fetch first, as (url, kind). Plugins can add known about/contact URLs here."""
        return [(f"https://{domain}/", "home")]

    def follow(self, page_url: str, hrefs: list[str], domain: str, kind: str) -> list[dict]:
        """Links to fetch next from this page: [{"url", "kind", "priority"}]."""
        return follow_links(page_url, hrefs, domain) if kind == "home" else []

    # -- parsing (parser) ------------------------------------------------------------------

    def extract(self, page: dict, tree: LexborHTMLParser, html: str, *, url: str, domain: str, country: str,
                kind: str) -> dict:
        """Adjust the generic result ({"title", "lang", "text", "facts", "links"}) for this site.

        `tree` has scripts and styles removed; re-parse `html` if you need them. Add facts with
        source "site" so the profile shows they came from a custom parser.
        """
        return page

    @property
    def parser_tag(self) -> str:
        return "" if self.name == "default" else f"+{self.name}@{self.version}"
