"""Public (logged-out) LinkedIn company page parser.

Only the main company page is public; everything else (people, jobs, posts pages) is behind the login wall.
Fields come from the top card (`h1`, headline, sublines), the About list (`[data-test-id=about-us__*]` dt/dd
pairs), the locations (`#address-N`) and the JSON-LD posts. The website link is unwrapped from LinkedIn's
`/redir/redirect?url=` link.

The login wall shows up two ways: the loader gets a redirect script to `/authwall` (load-page answers 500, the
loader retries), or a 200 "LinkedIn Login, Sign in" page, which raises BlockedError so the caller backs off and
retries. LinkedIn serves the public page to the same URL on some requests and the wall on others.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, unquote, urlsplit

from app.core.exceptions import BlockedError, ParseError
from app.core.models import LinkedInCompany, LinkedInPost, PageResponse
from app.core.utils import is_http_url, normalize_whitespace
from app.parse.base_parser import BaseParser, register_parser
from app.parse.html_parser import HtmlDocument, node_text

COMPANY_URL = re.compile(r"^https?://(?:[a-z]{2,3}\.)?linkedin\.com/company/([^/?#\s]+)", re.I)
_LOGIN_TITLE = re.compile(r"Sign in|Log ?in|Sign Up", re.I)
_NOT_FOUND = re.compile(r"Page not found|this page doesn.t exist", re.I)
_COUNT = re.compile(r"([\d,.]+)\s*([KkMm])?")
_FOLLOWERS = re.compile(r"([\d,.]+\s*[KkMm]?)\s+followers", re.I)
_EMPLOYEES = re.compile(r"View all ([\d,]+) employees", re.I)


def company_slug(url: str) -> str | None:
    """`https://pk.linkedin.com/company/sadaqatgroup/about` -> `sadaqatgroup` (None for other LinkedIn pages)."""
    match = COMPANY_URL.match(url.strip())
    return unquote(match.group(1)).strip().lower() if match else None


def company_url(slug: str) -> str:
    return f"https://www.linkedin.com/company/{slug}/"


def parse_count(text: str | None) -> int | None:
    """`67,771` -> 67771, `1.2K` -> 1200."""
    if not text:
        return None
    match = _COUNT.search(text)
    if not match:
        return None
    try:
        number = float(match.group(1).replace(",", ""))
    except ValueError:
        return None
    scale = {"k": 1_000, "m": 1_000_000}.get((match.group(2) or "").lower(), 1)
    return int(number * scale)


def _unwrap_redirect(href: str) -> str | None:
    parts = urlsplit(href)
    if parts.hostname and parts.hostname.endswith("linkedin.com") and "redir" in parts.path:
        target = (parse_qs(parts.query).get("url") or [""])[0]
        return target if is_http_url(target) else None
    return href if is_http_url(href) else None


@register_parser
class LinkedInCompanyParser(BaseParser[LinkedInCompany]):
    name = "linkedin_company_parser"

    def parse(self, page: PageResponse) -> LinkedInCompany:
        html = page.html
        doc = HtmlDocument(html, page.final_url)
        name = node_text(doc.css_first("h1.top-card-layout__title") or doc.css_first(".top-card-layout h1"))
        if not name or _LOGIN_TITLE.fullmatch(name):
            if "authwall" in html[:20_000] or _LOGIN_TITLE.search(doc.title or ""):
                raise BlockedError("LinkedIn served the login wall")
            if _NOT_FOUND.search(doc.title or "") or _NOT_FOUND.search(html[:50_000]):
                raise ParseError("LinkedIn company page not found")
            raise ParseError("no company name on the LinkedIn page: layout changed or not a company page")

        about: dict[str, str] = {}
        website = None
        for node in doc.css('[data-test-id^="about-us__"]'):
            key = (node.attributes.get("data-test-id") or "").split("__", 1)[-1]
            value_node = node.css_first("dd") or node
            if key == "website":
                link = value_node.css_first("a[href]")
                website = _unwrap_redirect(link.attributes.get("href") or "") if link else None
                website = website or (node_text(value_node).split(" ")[0] or None)
            else:
                about[key] = node_text(value_node)

        founded = about.get("foundedOn", "")
        subline = node_text(doc.css_first(".top-card-layout__first-subline"))
        followers = _FOLLOWERS.search(subline) or _FOLLOWERS.search(doc.meta.get("description", ""))
        employees = _EMPLOYEES.search(node_text(doc.css_first('[data-test-id="view-all-employees-cta"]')))
        logo = doc.css_first("img.top-card-layout__entity-image")
        logo_url = (logo.attributes.get("data-delayed-url") or logo.attributes.get("src")) if logo else None
        return LinkedInCompany(
            url=page.final_url, name=name,
            tagline=node_text(doc.css_first(".top-card-layout__second-subline")) or None,
            description=about.get("description") or doc.meta.get("og:description") or None,
            industry=about.get("industry") or node_text(doc.css_first(".top-card-layout__headline")) or None,
            website=website, company_size=about.get("size") or None,
            headquarters=about.get("headquarters") or None, organization_type=about.get("organizationType") or None,
            founded_year=int(founded) if founded.isdigit() else None,
            specialties=_split_specialties(about.get("specialties", "")),
            followers=parse_count(followers.group(1)) if followers else None,
            employees_on_linkedin=parse_count(employees.group(1)) if employees else None,
            locations=[text for node in doc.css('[id^="address-"]') if (text := node_text(node))],
            logo_url=logo_url or doc.meta.get("og:image") or None,
            recent_posts=_posts(doc))


def _split_specialties(text: str) -> list[str]:
    """`Fintech app, Ecommerce app, and Startup` -> three items."""
    parts = re.split(r",\s*(?:and\s+)?|\s+and\s+(?=[^,]*$)", text) if text else []
    return [p.strip() for p in parts if p.strip()]


def _posts(doc: HtmlDocument) -> list[LinkedInPost]:
    posts: list[LinkedInPost] = []
    for block in doc.json_ld:
        items = block.get("@graph") if isinstance(block.get("@graph"), list) else [block]
        for item in items:
            if not isinstance(item, dict) or item.get("@type") != "SocialMediaPosting":
                continue
            text = normalize_whitespace(item.get("text") or item.get("articleBody") or "")
            if not text:
                continue
            stats = item.get("interactionStatistic")
            likes = stats.get("userInteractionCount") if isinstance(stats, dict) else None
            posts.append(LinkedInPost(text=text[:3000], url=item.get("url") or item.get("mainEntityOfPage"),
                                      published_at=item.get("datePublished"),
                                      likes=likes if isinstance(likes, int) else None))
    return posts

