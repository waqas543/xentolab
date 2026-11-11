"""Source-independent normalizers used by every Silver transform."""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from urllib.parse import parse_qs, urlsplit

import phonenumbers
import tldextract

NORMALIZER_VERSION = "norm-v3"  # v3: display names, chain names, phone kinds, postal codes, opening hours
# v2: more platforms, private public suffixes

# Legal-form and filler tokens dropped from the matching key (not from display names).
LEGAL_TOKENS = {
    "ltd", "limited", "pvt", "private", "inc", "incorporated", "llc", "llp", "plc", "co", "company", "corp",
    "corporation", "gmbh", "sa", "ag", "bv", "the", "and", "smc", "pakistan",
}

# Sites that host many businesses: a page there is not the business's own domain.
PLATFORM_DOMAINS = {
    "facebook.com", "fb.com", "instagram.com", "twitter.com", "x.com", "linkedin.com", "youtube.com",
    "tiktok.com", "wa.me", "whatsapp.com", "linktr.ee", "google.com", "goo.gl", "business.site",
    "blogspot.com", "wordpress.com", "wixsite.com", "yelp.com", "tripadvisor.com", "foursquare.com",
    "pinterest.com", "t.me", "telegram.me", "daraz.pk", "olx.com.pk", "sites.google.com",
    # Webmail, link shorteners and map links: an address there is not a website.
    "gmail.com", "googlemail.com", "yahoo.com", "hotmail.com", "outlook.com", "live.com", "icloud.com",
    "bit.ly", "tinyurl.com", "t.co", "g.co", "g.page", "share.google", "fb.me", "m.me", "wa.link", "forms.gle",
    "youtu.be", "maps.app.goo.gl",
    # Site builders, hosts, encyclopedias and marketplaces whose root is not one business.
    "webs.com", "weebly.com", "wix.com", "squarespace.com", "jimdo.com", "site123.me", "godaddysites.com",
    "myshopify.com", "vercel.app", "netlify.app", "github.io", "herokuapp.com", "wikipedia.org", "wikimedia.org",
    "alibaba.com", "amazon.com", "aliexpress.com", "foodpanda.pk", "zameen.com", "justdial.com", "yellowpages.pk",
}

_EMAIL = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_SPACE = re.compile(r"\s+")


@lru_cache(maxsize=1)
def _tld_extractor() -> tldextract.TLDExtract:
    # Bundled public-suffix snapshot only: no network access, deterministic results. Private suffixes are
    # included, so shop.myshopify.com or name.blogspot.com is its own site rather than one huge platform domain.
    return tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None, include_psl_private_domains=True)


def first_value(value: str | None) -> str | None:
    """OSM and others pack several values into one field with ';'. Take the first non-empty one."""
    if value is None:
        return None
    for part in str(value).split(";"):
        part = part.strip()
        if part:
            return part
    return None


def normalize_name(name: str | None) -> str | None:
    if not name:
        return None
    text = unicodedata.normalize("NFKC", name).casefold()
    text = _SPACE.sub(" ", _PUNCT.sub(" ", text)).strip()
    return text or None


def name_key(name: str | None) -> str | None:
    """Matching key: normalized name without legal forms/fillers ("Systems (Pvt.) Ltd" -> "systems")."""
    normalized = normalize_name(name)
    if not normalized:
        return None
    tokens = [t for t in normalized.split(" ") if t not in LEGAL_TOKENS]
    return " ".join(tokens) or normalized


def normalize_website(value: str | None) -> tuple[str | None, str | None, str | None, bool]:
    """Return (url, registrable_domain, tld, is_platform). Invalid input -> (None, None, None, False)."""
    raw = first_value(value)
    if not raw:
        return None, None, None, False
    raw = raw.strip().strip("\"'<>")
    if "@" in raw and "/" not in raw:
        return None, None, None, False          # an email in the website field
    url = raw if re.match(r"^[a-z][a-z0-9+.-]*://", raw, re.I) else f"http://{raw}"
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").rstrip(".").lower()
    except ValueError:
        return None, None, None, False
    if not host or " " in host or "." not in host:
        return None, None, None, False
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        return None, None, None, False
    extracted = _tld_extractor()(host)
    if not extracted.suffix or not extracted.domain:
        return None, None, None, False
    domain = f"{extracted.domain}.{extracted.suffix}"
    is_platform = domain in PLATFORM_DOMAINS or host in PLATFORM_DOMAINS
    url = parts._replace(scheme=parts.scheme.lower(), netloc=parts.netloc.lower()).geturl()
    return url, domain, extracted.suffix.rsplit(".", 1)[-1], is_platform


def normalize_phone(value: str | None, country_code: str | None) -> str | None:
    """E.164 if the number is valid for the given/implied country, else None."""
    raw = first_value(value)
    if not raw:
        return None
    raw = raw.replace("(0)", "")
    try:
        parsed = phonenumbers.parse(raw, (country_code or "").upper() or None)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(parsed):
        return None
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def normalize_email(value: str | None) -> str | None:
    raw = first_value(value)
    if not raw:
        return None
    raw = raw.strip().lower().removeprefix("mailto:")
    return raw if _EMAIL.match(raw) else None


def valid_coordinates(lat, lon) -> bool:
    return (lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180
            and not (lat == 0 and lon == 0))


@lru_cache(maxsize=200_000)
def registered_domain(host: str | None) -> tuple[str | None, str | None]:
    """(registrable domain, public suffix) of a host name, e.g. shop.example.com.pk -> (example.com.pk, com.pk)."""
    if not host:
        return None, None
    parts = _tld_extractor()(host.strip().lower().rstrip("."))
    if not parts.domain or not parts.suffix:
        return None, parts.suffix or None
    return f"{parts.domain}.{parts.suffix}", parts.suffix


def surt_host_prefix(domain: str) -> str:
    """SURT form of a domain as used by web-archive indexes: example.com.pk -> pk,com,example"""
    return ",".join(reversed(domain.lower().strip(".").split(".")))


# Social profiles: canonical profile URLs; share buttons, posts and videos are not profiles.

_HANDLE = re.compile(r"^@?[A-Za-z0-9._-]{2,100}$")

_FB_DENY = {"sharer", "sharer.php", "share", "share.php", "dialog", "plugins", "tr", "login", "login.php", "events",
            "hashtag", "watch", "story.php", "photo", "photo.php", "groups", "home.php", "help", "policies", "privacy"}
_IG_DENY = {"p", "reel", "reels", "explore", "stories", "accounts", "tv", "share", "direct"}
_X_DENY = {"intent", "share", "home", "hashtag", "search", "i", "login", "signup", "privacy", "tos", "explore"}
_PIN_DENY = {"pin", "search", "ideas", "today"}


def _host(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    for prefix in ("www.", "m.", "mobile.", "web.", "business.", "pk.", "en."):
        host = host.removeprefix(prefix)
    return host


def social_profile(url: str) -> tuple[str, str] | None:
    """(network, canonical profile URL) for links to a profile/page; None for share buttons, posts, etc."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    host = _host(url)
    segs = [s for s in parts.path.split("/") if s]
    first = segs[0].lower() if segs else ""

    def handle(i: int = 0) -> str | None:
        return segs[i] if len(segs) > i and _HANDLE.match(segs[i]) else None

    if host in ("facebook.com", "fb.com", "fb.me"):
        if first == "profile.php" and (pid := parse_qs(parts.query).get("id")):
            return "facebook", f"https://www.facebook.com/profile.php?id={pid[0]}"
        if first in ("pages", "people") and len(segs) >= 3:
            return "facebook", "https://www.facebook.com/" + "/".join(segs[:3])
        if first and first not in _FB_DENY and handle():
            return "facebook", f"https://www.facebook.com/{segs[0]}"
    elif host == "instagram.com":
        if first and first not in _IG_DENY and handle():
            return "instagram", f"https://www.instagram.com/{segs[0]}"
    elif host in ("twitter.com", "x.com"):
        if first and first not in _X_DENY and handle():
            return "x_twitter", f"https://x.com/{segs[0]}"
    elif host == "linkedin.com":
        if first in ("company", "in", "school", "showcase") and handle(1):
            return "linkedin", f"https://www.linkedin.com/{first}/{segs[1]}"
    elif host in ("youtube.com", "youtu.be"):
        if first.startswith("@") and handle():
            return "youtube", f"https://www.youtube.com/{segs[0]}"
        if first in ("channel", "c", "user") and handle(1):
            return "youtube", f"https://www.youtube.com/{first}/{segs[1]}"
    elif host == "tiktok.com":
        if first.startswith("@") and handle():
            return "tiktok", f"https://www.tiktok.com/{segs[0]}"
    elif host == "pinterest.com" or host.startswith("pinterest."):
        if first and first not in _PIN_DENY and handle():
            return "pinterest", f"https://www.pinterest.com/{segs[0]}"
    elif host == "wa.me":
        if first.isdigit():
            return "whatsapp", f"https://wa.me/{first}"
    elif host in ("api.whatsapp.com", "whatsapp.com", "chat.whatsapp.com"):
        if first == "send" and (phone := parse_qs(parts.query).get("phone")):
            digits = re.sub(r"\D", "", phone[0])
            return ("whatsapp", f"https://wa.me/{digits}") if digits else None
        if first == "channel" and handle(1):
            return "whatsapp", f"https://whatsapp.com/channel/{segs[1]}"
    elif host in ("threads.net", "threads.com"):
        if first.startswith("@") and handle():
            return "threads", f"https://www.threads.net/{segs[0]}"
    return None


# ---------------------------------------------------------------- display names
# Listings are often written as adverts: "Ressichem | Epoxy | Plaster", "Dr. Ali 03001234567", "ALI TRADERS",
# or a Latin name followed by its Urdu spelling. `clean_name` gives the name to display; the original is kept.

_PHONE_IN_TEXT = re.compile(r"(?:\+|00)?\d[\d\s().-]{6,}\d")
_STUFFING_SEPARATORS = re.compile(r"\s*(?:\||｜|•|·)\s*")
_NON_LATIN_SCRIPT = re.compile(r"[֐-ࣿיִ-﷿ﹰ-﻿ऀ-෿]")   # Hebrew..Arabic..Indic
_LATIN_LETTER = re.compile(r"[A-Za-z]")
_SMALL_WORDS = {"a", "an", "and", "at", "by", "for", "in", "of", "on", "or", "the", "to", "&"}
_ALWAYS_TITLE = {"THE", "AND", "FOR", "OF", "AT", "BY", "IN", "ON", "TO", "OR", "NEW", "OLD", "BIG", "TOP", "ONE",
                 "TWO", "SUN", "STAR", "AL", "EL", "DR", "MR", "MRS", "MS", "SIR", "SHOP", "HUB", "BAR", "INN", "SPA"}


def _tidy(text: str) -> str:
    text = _SPACE.sub(" ", text).strip(" -–—,;:|/")
    return re.sub(r"\(\s*\)", "", text).strip()


def _fix_capitals(text: str) -> str:
    """ALL-CAPS names to title case; short all-caps words (MCB, HBL, PSO) stay as acronyms."""
    letters = [c for c in text if c.isalpha() and c.isascii()]
    if len(letters) < 6 or sum(c.isupper() for c in letters) / len(letters) < 0.9:
        return text
    words = []
    for index, word in enumerate(text.split(" ")):
        core = re.sub(r"[^A-Za-z]", "", word)
        if len(core) <= 3 and core.upper() not in _ALWAYS_TITLE and core:
            words.append(word)                                     # likely an acronym
        elif index and core.lower() in _SMALL_WORDS:
            words.append(word.lower())
        else:
            words.append(re.sub(r"[A-Za-z]+", lambda m: m.group(0).capitalize(), word))
    return " ".join(words)


def split_scripts(name: str) -> tuple[str, str | None]:
    """("Sakhawat Kabab House", "سخاوت کباب ہاؤس") for a name written in a Latin and a non-Latin script."""
    if not _NON_LATIN_SCRIPT.search(name) or not _LATIN_LETTER.search(name):
        return name, None
    latin = _tidy(_NON_LATIN_SCRIPT.sub(" ", name))
    local = _tidy(re.sub(r"[A-Za-z0-9]", " ", "".join(c for c in name if not c.isascii() or c == " ")))
    if len(re.sub(r"[^A-Za-z]", "", latin)) < 3:
        return name, None
    return latin, (local or None)


def clean_name(name: str | None, brand: str | None = None) -> tuple[str | None, str | None]:
    """(display name, local-script name) for a listing name. Returns (None, None) for an empty name.

    Removes advert stuffing after a separator, phone numbers and ALL CAPS, and splits a trailing local-script
    spelling off into its own field. A name written only in a local script shows the brand's Latin name when
    the listing has one ("شیل" with brand Shell -> "Shell").
    """
    if not name or not name.strip():
        return None, None
    text = unicodedata.normalize("NFKC", name).strip()
    text, local = split_scripts(text)
    parts = [p for p in _STUFFING_SEPARATORS.split(text) if p.strip()]
    if len(parts) > 1 and len(parts[0].strip()) >= 3:
        text = parts[0]
    without_phone = _tidy(_PHONE_IN_TEXT.sub(" ", text))
    if len(without_phone) >= 3:
        text = without_phone
    text = _fix_capitals(_tidy(text))
    if not _LATIN_LETTER.search(text) and brand and _LATIN_LETTER.search(brand):
        local, text = local or text, brand.strip()
    return (text or name.strip()), local


# A chain's branches carry the brand plus a branch label: "Meezan Bank - Gulberg Branch", "The Educators: Jinnah
# Campus", "UBL Signature Branch Sukkur". `chain_name` is the brand part, used to name the organization.
_BRANCH_WORDS = r"(?:branch|branches|campus|outlet|franchise)"
_BRANCH_CUT = re.compile(r"\s*(?:\s[-–—]\s|:|\||\(|,|\bno\.?\s*\d)", re.IGNORECASE)
_BRANCH_WORD = re.compile(rf"\b{_BRANCH_WORDS}\b", re.IGNORECASE)
# Words that belong to the brand itself ("The Smart School", "Dar-e-Arqam School"), so a branch label never eats them.
_BRAND_TYPE_WORDS = {
    "school", "schools", "college", "colleges", "university", "academy", "institute", "system", "systems", "group",
    "bank", "lab", "labs", "laboratory", "hospital", "clinic", "pharmacy", "restaurant", "hotel", "cafe", "bakery",
    "sweets", "foods", "mart", "store", "stores", "traders", "motors", "station", "pump", "courier", "express",
    "international", "limited", "ltd", "pvt", "company", "corporation", "enterprises", "industries", "network",
}


def _strip_branch_label(text: str) -> str:
    """'UBL Signature Branch Sukkur' -> 'UBL'; 'The Smart School Nasir Bagh Campus' -> 'The Smart School'."""
    match = _BRANCH_WORD.search(text)
    if not match or match.start() == 0:
        return text
    words = text[:match.start()].split()
    kept = len(words)
    for _ in range(3):                    # a branch label is at most three words before "branch"/"campus"
        if kept <= 1 or re.sub(r"[^a-z]", "", words[kept - 1].lower()) in _BRAND_TYPE_WORDS:
            break
        if kept == 2 and re.sub(r"[^a-z]", "", words[0].lower()) in _BRAND_TYPE_WORDS:
            break                         # never shrink "Bank Alfalah ..." to a bare "Bank"
        kept -= 1
    return " ".join(words[:kept])


def chain_name(name: str | None, places: tuple[str, ...] = ()) -> str | None:
    """The brand part of a branch name, without the branch label or the town it is in."""
    display, _ = clean_name(name)
    if not display:
        return None
    text = _BRANCH_CUT.split(display, maxsplit=1)[0].strip() or display
    trimmed = _strip_branch_label(text).strip()
    if len(trimmed) >= 3:
        text = trimmed
    for place in sorted({p for p in places if p}, key=len, reverse=True):
        stripped = re.sub(rf"\s+{re.escape(place)}$", "", text, flags=re.IGNORECASE).strip()
        if len(stripped) >= 3:
            text = stripped
    text = _tidy(text)
    # Never a bare article or filler ("The: Lahore Lyceum" must not become "The").
    if not text or len(re.sub(r"[^A-Za-z]", "", text)) < 3 or text.lower() in _SMALL_WORDS | {"the", "al", "new"}:
        return display
    return text


# ---------------------------------------------------------------- phones, postal codes, opening hours

# Numbers that reach a head office or call centre rather than one location: Pakistan's UAN (111 ...) and
# toll-free (0800 ...) lines, and international toll-free prefixes.
_HOTLINE_PATTERNS = {
    "PK": re.compile(r"^\+92(?:\d{1,2}111\d{6}|800\d{5,6}|111\d{6})$"),
}
_GENERIC_HOTLINE = re.compile(r"^\+(?:1(?:800|888|877|866|855|844|833)|44(?:800|808)|91(?:1800))\d+$")


def is_hotline(phone: str | None, country_code: str | None) -> bool:
    """True for an E.164 number that is a shared head-office / toll-free line, by its numbering pattern."""
    if not phone:
        return False
    pattern = _HOTLINE_PATTERNS.get((country_code or "").upper())
    return bool((pattern and pattern.match(phone)) or _GENERIC_HOTLINE.match(phone))


_POSTAL_PATTERNS = {
    "PK": r"\d{5}", "IN": r"\d{6}", "BD": r"\d{4}", "AE": None, "SA": r"\d{5}(?:-\d{4})?", "GB": r"[A-Z]{1,2}\d[A-Z\d]? ?\d[A-Z]{2}",
    "US": r"\d{5}(?:-\d{4})?", "DE": r"\d{5}", "FR": r"\d{5}", "TR": r"\d{5}", "MY": r"\d{5}", "EG": r"\d{5}",
}


def normalize_postal_code(value: str | None, country_code: str | None) -> str | None:
    """The postal code when it has the country's format, else None (countries without a known format pass)."""
    if not value:
        return None
    text = str(value).strip().upper()
    code = (country_code or "").upper()
    if code in _POSTAL_PATTERNS:
        pattern = _POSTAL_PATTERNS[code]
        if pattern is None:
            return None                                  # the country has no postal codes
        return text if re.fullmatch(pattern, text) else None
    return text if re.fullmatch(r"[A-Z0-9][A-Z0-9 -]{1,9}", text) else None


_DAYS = {"monday": "Mo", "tuesday": "Tu", "wednesday": "We", "thursday": "Th", "friday": "Fr", "saturday": "Sa",
         "sunday": "Su"}
_DAY_ORDER = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
_TIME = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*([AaPp])\.?\s*[Mm]\.?")


def _clock(text: str) -> str | None:
    match = _TIME.search(text)
    if not match:
        return None
    hour, minute, half = int(match.group(1)) % 12, int(match.group(2) or 0), match.group(3).lower()
    return f"{hour + (12 if half == 'p' else 0):02d}:{minute:02d}"


def parse_google_hours(text: str | None) -> str | None:
    """OSM-style opening_hours from a Google Maps hours text, or None when it is only a status snapshot.

    "Open 24 hours" -> "24/7"; "Monday: 9 AM to 5 PM; ...; Sunday: Closed" -> "Mo 09:00-17:00; ...; Su off".
    Snapshots such as "Open · Closes 10 PM" describe one moment, not a schedule, and are ignored.
    """
    if not text:
        return None
    raw = text.strip()
    if raw.lower() in ("open 24 hours", "24 hours"):
        return "24/7"
    days: dict[str, str] = {}
    for part in raw.split(";"):
        day, _, hours = part.partition(":")
        key = _DAYS.get(day.strip().lower())
        if not key:
            continue
        hours = hours.split(",")[0].strip().lower()
        if hours.startswith("closed"):
            days[key] = "off"
        elif "24 hours" in hours:
            days[key] = "00:00-24:00"
        elif " to " in hours:
            start, end = (_clock(h) for h in hours.split(" to ", 1))
            if start and end:
                days[key] = f"{start}-{end}"
    if len(days) < 7:
        return None
    if all(days[d] == "00:00-24:00" for d in _DAY_ORDER):
        return "24/7"
    return "; ".join(f"{d} {days[d]}" for d in _DAY_ORDER)
