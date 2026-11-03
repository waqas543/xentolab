"""Deterministic extraction from one HTML page: pure functions, no I/O.

Each fact is {"field", "value", "source", "evidence"?}. Sources: jsonld (schema.org markup), meta (meta/OG
tags), link (anchors: social profiles, mailto:, tel:), text (patterns in visible text).
"""

from __future__ import annotations

import json
import re
from urllib.parse import urljoin, urlsplit, urlunsplit

import phonenumbers
from selectolax.lexbor import LexborHTMLParser

from ..normalize import normalize_email, normalize_phone, registered_domain, social_profile

TEXT_LIMIT = 20_000
MAX_LINKS = 8
MAX_CONTACTS = 5
_SPACE = re.compile(r"\s+")
_CHARSET = re.compile(rb"""<meta[^>]+charset=["']?([A-Za-z0-9_-]+)""", re.I)
_EMAIL_TEXT = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_ASSET_SUFFIX = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".js", ".css")

# Same-site pages worth a visit, by last path segment: (kind, priority). Lower priority is fetched first.
FOLLOW = {
    "about": ("about", 1), "about-us": ("about", 1), "aboutus": ("about", 1), "about_us": ("about", 1),
    "who-we-are": ("about", 1), "our-story": ("about", 1), "company": ("company", 2), "company-profile": ("company", 2),
    "profile": ("company", 3), "overview": ("company", 3), "contact": ("contact", 2), "contact-us": ("contact", 2),
    "contactus": ("contact", 2), "contact_us": ("contact", 2), "get-in-touch": ("contact", 2),
    "team": ("team", 4), "our-team": ("team", 4), "leadership": ("team", 4), "management": ("team", 4),
}
_PAGE_EXTENSION = re.compile(r"\.(html?|php|aspx?)$", re.I)

ORG_TYPES = ("Organization", "Corporation", "LocalBusiness", "NewsMediaOrganization", "Store", "OnlineStore",
             "MedicalOrganization", "EducationalOrganization", "Hospital", "Restaurant", "Hotel", "Pharmacy",
             "ProfessionalService", "FinancialService", "Airline", "NGO")


# ---------------------------------------------------------------- decoding

def decode(body: bytes, content_type: str | None) -> str:
    charset = None
    if content_type and "charset=" in content_type.lower():
        charset = content_type.lower().split("charset=")[-1].split(";")[0].strip(" \"'")
    if not charset and (match := _CHARSET.search(body[:4096])):
        charset = match.group(1).decode("ascii", "ignore")
    try:
        return body.decode(charset or "utf-8", errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


# ---------------------------------------------------------------- JSON-LD

def jsonld_nodes(scripts: list[str]):
    for raw in scripts:
        try:
            data = json.loads(raw.strip().rstrip(";"), strict=False)
        except ValueError:
            continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, dict):
                yield node
                stack.extend(v for v in node.values() if isinstance(v, (dict, list)))


def _is_org(node: dict) -> bool:
    types = node.get("@type")
    types = types if isinstance(types, list) else [types]
    return any(isinstance(t, str) and (t in ORG_TYPES or t.endswith(("Organization", "Business", "Store")))
               for t in types)


def _text(value) -> str | None:
    if isinstance(value, dict):
        value = value.get("name") or value.get("@value") or value.get("value")
    if isinstance(value, list):
        value = value[0] if value else None
    if value is None or isinstance(value, (dict, list)):
        return None
    value = _SPACE.sub(" ", str(value)).strip()
    return value or None


def jsonld_facts(nodes, country: str) -> list[dict]:
    facts = []

    def add(field, value, evidence=None):
        if value:
            facts.append({"field": field, "value": str(value)[:1000], "source": "jsonld", "evidence": evidence})

    for node in nodes:
        if not _is_org(node):
            continue
        add("company_name", _text(node.get("legalName")) or _text(node.get("name")))
        add("description", _text(node.get("description")))
        founded = _text(node.get("foundingDate"))
        if founded and (year := re.match(r"(1[89]\d\d|20\d\d)", founded)):
            add("founded_year", year.group(1), founded)
        employees = node.get("numberOfEmployees")
        if isinstance(employees, dict) and ("minValue" in employees or "maxValue" in employees):
            add("employees", f"{employees.get('minValue', '')}-{employees.get('maxValue', '')}".strip("-"))
        else:
            add("employees", _text(employees))
        addresses = node.get("address")
        for address in addresses if isinstance(addresses, list) else [addresses]:
            if isinstance(address, dict):
                add("address_locality", _text(address.get("addressLocality")))
                parts = [_text(address.get(k)) for k in ("streetAddress", "addressLocality", "addressRegion")]
                add("address", ", ".join(p for p in parts if p))
            elif isinstance(address, str):
                add("address", _text(address))
        if phone := normalize_phone(_text(node.get("telephone")), country):
            add("phone", phone)
        if email := normalize_email(_text(node.get("email"))):
            add("email", email)
        same_as = node.get("sameAs") or []
        for url in same_as if isinstance(same_as, list) else [same_as]:
            if isinstance(url, str) and (profile := social_profile(url)):
                add(f"social.{profile[0]}", profile[1])
    return facts


# ---------------------------------------------------------------- page

def same_site(url: str, domain: str) -> bool:
    return registered_domain(urlsplit(url).hostname)[0] == domain


def follow_links(base_url: str, hrefs: list[str], domain: str) -> list[dict]:
    """About/contact/team pages on the same site, best first."""
    found: dict[str, dict] = {}
    base = base_url.split("#")[0].rstrip("/")
    for href in hrefs:
        try:
            url = urljoin(base_url, href.strip())
            parts = urlsplit(url)
        except ValueError:
            continue
        if parts.scheme not in ("http", "https") or not same_site(url, domain):
            continue
        segs = [s for s in parts.path.split("/") if s]
        if not segs or len(segs) > 2:
            continue
        match = FOLLOW.get(_PAGE_EXTENSION.sub("", segs[-1].lower()))
        if not match:
            continue
        clean = urlunsplit((parts.scheme, parts.netloc.lower(), parts.path, "", ""))
        if clean.rstrip("/") == base or clean in found:
            continue
        found[clean] = {"url": clean, "kind": match[0], "priority": match[1] + (len(segs) - 1)}
    return sorted(found.values(), key=lambda link: (link["priority"], len(link["url"])))[:MAX_LINKS]


def parse_html(html: str, *, url: str, domain: str, country: str, kind: str, site=None) -> dict:
    """Everything the parser extracts from one page: title, lang, text, facts, links to follow.

    `site` (a sites.Site plugin) decides which links to follow and may adjust the result.
    """
    tree = LexborHTMLParser(html)
    head_title = tree.css_first("title")
    title = _SPACE.sub(" ", head_title.text()).strip()[:500] if head_title else None
    root = tree.css_first("html")
    lang = (root.attributes.get("lang") or "").strip()[:20] if root else ""

    meta = {}
    for node in tree.css("meta"):
        key = (node.attributes.get("name") or node.attributes.get("property") or "").lower()
        if key and node.attributes.get("content") and key not in meta:
            meta[key] = node.attributes["content"]

    scripts = [node.text() for node in tree.css('script[type="application/ld+json"]')]
    hrefs = [node.attributes.get("href") or "" for node in tree.css("a[href]")]

    facts = jsonld_facts(jsonld_nodes(scripts), country)
    if site_name := _text(meta.get("og:site_name")):
        facts.append({"field": "company_name", "value": site_name, "source": "meta"})
    if description := _text(meta.get("description") or meta.get("og:description")):
        facts.append({"field": "description", "value": description[:1000], "source": "meta"})

    seen: set[tuple[str, str]] = set()

    def add_link_fact(field: str, value: str):
        if (field, value) not in seen:
            seen.add((field, value))
            facts.append({"field": field, "value": value, "source": "link"})

    emails, phones = set(), set()
    for href in hrefs:
        lowered = href.strip().lower()
        if lowered.startswith("mailto:"):
            if (email := normalize_email(lowered.split("?")[0])) and len(emails) < MAX_CONTACTS:
                emails.add(email)
                add_link_fact("email", email)
        elif lowered.startswith("tel:"):
            if (phone := normalize_phone(href.strip()[4:], country)) and len(phones) < MAX_CONTACTS:
                phones.add(phone)
                add_link_fact("phone", phone)
        elif profile := social_profile(urljoin(url, href)):
            add_link_fact(f"social.{profile[0]}", profile[1])

    for node in tree.css("script, style, noscript, svg, template, iframe"):
        node.decompose()
    body = tree.body
    text = _SPACE.sub(" ", body.text(separator=" ")).strip()[:TEXT_LIMIT] if body else ""

    # Contact details written as text: only on about/contact pages, where they describe the company itself.
    if kind != "home" and text:
        for email in _EMAIL_TEXT.findall(text):
            email = normalize_email(email)
            if email and not email.endswith(_ASSET_SUFFIX) and email not in emails and len(emails) < MAX_CONTACTS:
                emails.add(email)
                facts.append({"field": "email", "value": email, "source": "text"})
        for match in phonenumbers.PhoneNumberMatcher(text[:TEXT_LIMIT], country or None):
            phone = phonenumbers.format_number(match.number, phonenumbers.PhoneNumberFormat.E164)
            if phone not in phones and len(phones) < MAX_CONTACTS:
                phones.add(phone)
                facts.append({"field": "phone", "value": phone, "source": "text", "evidence": match.raw_string})

    if site is None:
        links = follow_links(url, hrefs, domain) if kind == "home" else []
        return {"title": title, "lang": lang or None, "text": text, "facts": facts, "links": links}
    page = {"title": title, "lang": lang or None, "text": text, "facts": facts,
            "links": site.follow(url, hrefs, domain, kind)}
    return site.extract(page, tree, html, url=url, domain=domain, country=country, kind=kind)
