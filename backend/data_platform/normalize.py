"""Source-independent normalizers used by every Silver transform."""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from urllib.parse import parse_qs, urlsplit

import phonenumbers
import tldextract

NORMALIZER_VERSION = "norm-v2"  # v2: more platforms, private public suffixes

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
