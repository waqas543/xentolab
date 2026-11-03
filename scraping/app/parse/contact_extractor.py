"""Contact extraction: phone numbers, emails, locations and social profiles.

Pure functions work on plain text (search snippets); `ContactExtractor` adds HTML sources: tel:/mailto: links,
social links and schema.org JSON-LD. Phone validation uses libphonenumber for the configured region (PK):
+92 / 0092 / 92 / 03xx mobiles, area-code landlines (042, 021, 051 ...), UAN (111-xxx-xxx) and toll-free 0800.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from functools import lru_cache
from typing import Any
from urllib.parse import unquote

import phonenumbers
from phonenumbers import PhoneNumberType

from app.core.config import ExtractionRules
from app.core.models import ContactInfo, Location, PageResponse, PhoneNumber
from app.core.utils import normalize_whitespace, unique
from app.parse.base_parser import BaseParser, register_parser
from app.parse.html_parser import HtmlDocument

TEXT_LIMIT = 200_000
MAX_PER_KIND = 25

_KINDS = {PhoneNumberType.MOBILE: "mobile", PhoneNumberType.FIXED_LINE: "landline",
          PhoneNumberType.FIXED_LINE_OR_MOBILE: "landline", PhoneNumberType.UAN: "uan",
          PhoneNumberType.TOLL_FREE: "toll_free"}
_LABEL = re.compile(r"\s*(?:address)?\s*:")
_COMMAS = re.compile(r"\s*,(?:\s*,)*\s*")
_BARE_UAN = re.compile(r"(?<![\d+])(?:UAN[:\s#.-]*)?(111[\s.-]?\d{3}[\s.-]?\d{3})(?![\d])", re.I)

_EMAIL = re.compile(r"(?<![\w.+-])[A-Za-z0-9][A-Za-z0-9._%+-]*@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
                    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)*\.[A-Za-z]{2,24}(?![\w-])")
_EMAIL_JUNK_SUFFIX = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".js", ".css")
_EMAIL_JUNK_DOMAINS = ("example.com", "example.org", "domain.com", "email.com", "yourdomain.com", "sentry.io",
                       "wixpress.com", "sentry-next.wixpress.com")

SOCIAL_PATTERNS: dict[str, re.Pattern[str]] = {
    "linkedin": re.compile(r"^https?://(?:[a-z]{2,3}\.)?linkedin\.com/(?:company|in|school|showcase)/[^/?#\s]+", re.I),
    "facebook": re.compile(r"^https?://(?:www\.|m\.|web\.|business\.)?(?:facebook|fb)\.com/"
                           r"(?!sharer|share|dialog|plugins|tr\b|login|privacy|policies|help|home\.php)"
                           r"(?:profile\.php\?id=\d+|[^/?#\s]+)", re.I),
    "instagram": re.compile(r"^https?://(?:www\.)?instagram\.com/(?!p/|explore|accounts|reel|stories)[A-Za-z0-9_.]+",
                            re.I),
    "twitter": re.compile(r"^https?://(?:www\.|mobile\.)?(?:twitter|x)\.com/"
                          r"(?!intent|share|home|search|hashtag|i/|privacy|tos)[A-Za-z0-9_]{1,15}(?![A-Za-z0-9_])",
                          re.I),
    "youtube": re.compile(r"^https?://(?:www\.|m\.)?youtube\.com/(?:channel/[\w-]+|c/[\w.-]+|user/[\w.-]+|@[\w.-]+)",
                          re.I),
    "tiktok": re.compile(r"^https?://(?:www\.)?tiktok\.com/@[\w.]+", re.I),
}


# ---------------------------------------------------------------- phones


def extract_phones(text: str, region: str = "PK") -> list[PhoneNumber]:
    text = text[:TEXT_LIMIT]
    found: dict[str, PhoneNumber] = {}
    spans: list[tuple[int, int]] = []
    for match in phonenumbers.PhoneNumberMatcher(text, region, leniency=phonenumbers.Leniency.VALID):
        number = phonenumbers.format_number(match.number, phonenumbers.PhoneNumberFormat.E164)
        kind = _KINDS.get(phonenumbers.number_type(match.number), "other")
        found.setdefault(number, PhoneNumber(number=number, raw=match.raw_string, kind=kind))
        spans.append((match.start, match.end))
    for match in _BARE_UAN.finditer(text):
        if any(start <= match.start(1) < end for start, end in spans):
            continue
        digits = re.sub(r"\D", "", match.group(1))
        found.setdefault(digits, PhoneNumber(number=digits, raw=match.group(1), kind="uan"))
    return list(found.values())[:MAX_PER_KIND]


def phone_from_value(value: str, region: str = "PK") -> list[PhoneNumber]:
    """Phones from a structured field (tel: link, JSON-LD telephone) where the whole value is the number."""
    value = unquote(value).removeprefix("tel:").strip()
    phones = extract_phones(value, region)
    if phones:
        return phones
    try:
        parsed = phonenumbers.parse(value, region)
    except phonenumbers.NumberParseException:
        return []
    if not phonenumbers.is_valid_number(parsed):
        return []
    number = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    return [PhoneNumber(number=number, raw=value, kind=_KINDS.get(phonenumbers.number_type(parsed), "other"))]


# ---------------------------------------------------------------- emails + social


def clean_email(value: str) -> str | None:
    email = unquote(value).removeprefix("mailto:").split("?")[0].strip().strip(".").lower()
    if not _EMAIL.fullmatch(email):
        return None
    domain = email.rsplit("@", 1)[1]
    if email.endswith(_EMAIL_JUNK_SUFFIX) or domain in _EMAIL_JUNK_DOMAINS or domain.endswith(".sentry.io"):
        return None
    return email


def extract_emails(text: str) -> list[str]:
    emails = (clean_email(match.group(0)) for match in _EMAIL.finditer(text[:TEXT_LIMIT]))
    return unique(email for email in emails if email)[:MAX_PER_KIND]


def social_profile(url: str) -> tuple[str, str] | None:
    for platform, pattern in SOCIAL_PATTERNS.items():
        if match := pattern.match(url.strip()):
            return platform, match.group(0).rstrip("/")
    return None


def extract_social_links(urls: Iterable[str]) -> dict[str, list[str]]:
    links: dict[str, list[str]] = {}
    for url in urls:
        if profile := social_profile(url):
            platform, clean = profile
            if clean.lower() not in (u.lower() for u in links.get(platform, [])):
                links.setdefault(platform, []).append(clean)
    return {platform: urls[:5] for platform, urls in links.items()}


# ---------------------------------------------------------------- locations


class LocationMatcher:
    """Finds city/province mentions and address-like spans ("Plot 5, Sector 24, Korangi, Karachi")."""

    def __init__(self, rules: ExtractionRules) -> None:
        self.cities = rules.cities
        self.province_aliases = {alias.lower(): province for province, aliases in rules.provinces.items()
                                 for alias in [province, *aliases]}
        # Case-sensitive (Title or UPPER case) so ordinary words don't match city names.
        names = sorted(self.cities, key=len, reverse=True)
        self._city = re.compile(r"\b(" + "|".join(re.escape(v) for n in names for v in (n, n.upper())) + r")\b")
        aliases = sorted(self.province_aliases, key=len, reverse=True)
        self._province = re.compile(r"\b(" + "|".join(re.escape(a) for a in aliases) + r")\b", re.I)
        keywords = sorted(rules.address_keywords, key=len, reverse=True)
        self._keyword = re.compile(r"(?<![\w])(" + "|".join(re.escape(k) for k in keywords) + r")(?![\w])", re.I)

    def city_of(self, text: str) -> str | None:
        match = self._city.search(text)
        return match.group(1).title() if match else None

    def extract(self, text: str) -> list[Location]:
        text = text[:TEXT_LIMIT]
        locations: list[Location] = list(self._addresses(text))
        for name in unique(m.group(1).title() for m in self._city.finditer(text)):
            canonical = next((c for c in self.cities if c.lower() == name.lower()), name)
            locations.append(Location(text=canonical, kind="city", city=canonical, province=self.cities.get(canonical)))
        for province in unique(self.province_aliases[m.group(1).lower()] for m in self._province.finditer(text)):
            locations.append(Location(text=province, kind="province", province=province))
        return locations[:MAX_PER_KIND * 2]

    def _addresses(self, text: str) -> Iterator[Location]:
        spans: list[tuple[int, int]] = []
        for match in self._keyword.finditer(text):
            if _LABEL.match(text, match.end()):  # "Head Office: ..." is a label, the address follows it
                continue
            start = _span_start(text, match.start())
            stop = _first_break(text, match.end(), min(len(text), match.end() + 150))
            city_match = self._city.search(text, match.end(), stop)  # an address ends at its city
            if city_match is None:
                continue
            end = city_match.end()
            if spans and start <= spans[-1][1]:
                spans[-1] = (spans[-1][0], max(spans[-1][1], end))
            else:
                spans.append((start, end))
        for start, end in spans:
            address = _COMMAS.sub(", ", normalize_whitespace(text[start:end])).strip(" ,;-")
            if 10 <= len(address) <= 250:
                city = self.city_of(address)
                canonical = next((c for c in self.cities if city and c.lower() == city.lower()), city)
                yield Location(text=address, kind="address", city=canonical,
                               province=self.cities.get(canonical) if canonical else None)


def _span_start(text: str, index: int, max_back: int = 60) -> int:
    floor = max(0, index - max_back)
    for i in range(index - 1, floor - 1, -1):
        char = text[i]
        if char in "\n:|•;" or (char == "." and i + 1 < len(text) and text[i + 1] == " " and i > 0
                                 and text[i - 1].islower() and text[i - 2:i].lower() not in ("no", "st")):
            return i + 1
    return floor if floor == 0 else _next_word(text, floor)


def _first_break(text: str, start: int, end: int) -> int:
    for i in range(start, end):
        if text[i] in "\n|•":
            return i
    return end


def _next_word(text: str, index: int) -> int:
    while index < len(text) and not text[index].isspace():
        index += 1
    return index


@lru_cache(maxsize=8)
def _matcher(rules_json: str) -> LocationMatcher:
    return LocationMatcher(ExtractionRules.model_validate_json(rules_json))


def location_matcher(rules: ExtractionRules) -> LocationMatcher:
    return _matcher(rules.model_dump_json())


# ---------------------------------------------------------------- combined


def extract_from_text(text: str, rules: ExtractionRules) -> ContactInfo:
    """Contacts from plain text such as a search-result title + snippet."""
    return ContactInfo(phones=extract_phones(text, rules.default_region), emails=extract_emails(text),
                       locations=location_matcher(rules).extract(text))


def _jsonld_nodes(blocks: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    stack: list[Any] = list(blocks)
    while stack:
        node = stack.pop()
        if isinstance(node, list):
            stack.extend(node)
        elif isinstance(node, dict):
            yield node
            stack.extend(v for v in node.values() if isinstance(v, (dict, list)))


def _jsonld_contacts(blocks: list[dict[str, Any]], rules: ExtractionRules) -> ContactInfo:
    info = ContactInfo()
    matcher = location_matcher(rules)
    for node in _jsonld_nodes(blocks):
        phones: list[PhoneNumber] = []
        for key in ("telephone", "faxNumber"):
            for value in _strings(node.get(key)):
                phones.extend(phone_from_value(value, rules.default_region))
        emails = [e for value in _strings(node.get("email")) if (e := clean_email(value))]
        social = extract_social_links(_strings(node.get("sameAs")))
        locations: list[Location] = []
        if node.get("@type") == "PostalAddress":
            parts = [normalize_whitespace(str(node[k])) for k in ("streetAddress", "addressLocality", "addressRegion")
                     if isinstance(node.get(k), str) and node[k].strip()]
            if parts:
                address = ", ".join(unique(parts))
                city = matcher.city_of(address) or (node.get("addressLocality") or None)
                locations.append(Location(text=address, kind="address", city=city,
                                          province=rules.cities.get(city) if city else None))
        info = info.merge(ContactInfo(phones=phones, emails=emails, locations=locations, social_links=social))
    return info


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [v for v in value if isinstance(v, str)]
    return []


@register_parser
class ContactExtractor(BaseParser[ContactInfo]):
    name = "contact_extractor"

    def parse(self, page: PageResponse) -> ContactInfo:
        return self.extract(HtmlDocument(page.html, page.final_url))

    def extract(self, doc: HtmlDocument) -> ContactInfo:
        region = self.rules.default_region
        phones: list[PhoneNumber] = []
        emails: list[str] = []
        for link in doc.links:
            if link.url.startswith("tel:"):
                phones.extend(phone_from_value(link.url, region))
            elif link.url.startswith("mailto:") and (email := clean_email(link.url)):
                emails.append(email)
        from_links = ContactInfo(phones=phones, emails=emails,
                                 social_links=extract_social_links(link.url for link in doc.links))
        return (from_links
                .merge(_jsonld_contacts(doc.json_ld, self.rules))
                .merge(extract_from_text(doc.text, self.rules)))

    def extract_text(self, text: str) -> ContactInfo:
        return extract_from_text(text, self.rules)
