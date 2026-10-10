"""Reading facts out of Wikidata entity JSON (claims, labels, identifiers)."""

from __future__ import annotations

from datetime import datetime, timezone

# Properties whose values are other items (their labels are fetched so Silver can show names, not QIDs).
ITEM_PROPERTIES = {
    "P31": "instance_of", "P452": "industry", "P159": "headquarters", "P749": "parent", "P355": "subsidiaries",
    "P112": "founders", "P1716": "brands", "P17": "country", "P414": "stock_exchange", "P131": "located_in",
    "P127": "owned_by", "P1454": "legal_form",
}
# External identifiers kept on the organization (scheme -> property).
IDENTIFIERS = {
    "lei": "P1278", "isin": "P946", "opencorporates": "P1320", "crunchbase": "P2088", "linkedin": "P4264",
    "twitter": "P2002", "facebook": "P2013", "instagram": "P2003", "youtube": "P2397", "freebase": "P646",
    "google_kg": "P2671", "ticker": "P249", "permid": "P3347", "dun_bradstreet": "P2771",
}
SOCIAL_URLS = {"twitter": "https://twitter.com/{}", "facebook": "https://www.facebook.com/{}",
               "instagram": "https://www.instagram.com/{}", "linkedin": "https://www.linkedin.com/company/{}",
               "youtube": "https://www.youtube.com/channel/{}"}


def _ranked(entity: dict, prop: str) -> list[dict]:
    """Statements of a property, best rank first; deprecated statements are ignored."""
    statements = [s for s in entity.get("claims", {}).get(prop, []) if s.get("rank") != "deprecated"]
    return sorted(statements, key=lambda s: 0 if s.get("rank") == "preferred" else 1)


def _value(statement: dict):
    snak = statement.get("mainsnak", {})
    if snak.get("snaktype") != "value":
        return None
    return snak.get("datavalue", {}).get("value")


def item_ids(entity: dict, prop: str) -> list[str]:
    return [v["id"] for v in (_value(s) for s in _ranked(entity, prop)) if isinstance(v, dict) and "id" in v]


def strings(entity: dict, prop: str) -> list[str]:
    values = []
    for statement in _ranked(entity, prop):
        value = _value(statement)
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, dict) and "text" in value:            # monolingual text
            values.append(value["text"])
    return values


def first_string(entity: dict, prop: str) -> str | None:
    values = strings(entity, prop)
    return values[0] if values else None


def monolingual(entity: dict, prop: str, language: str = "en") -> str | None:
    texts = [_value(s) for s in _ranked(entity, prop)]
    texts = [t for t in texts if isinstance(t, dict) and "text" in t]
    preferred = [t["text"] for t in texts if t.get("language") == language]
    return preferred[0] if preferred else (texts[0]["text"] if texts else None)


def coordinates(entity: dict) -> tuple[float | None, float | None]:
    for statement in _ranked(entity, "P625"):
        value = _value(statement)
        if isinstance(value, dict) and "latitude" in value:
            return float(value["latitude"]), float(value["longitude"])
    return None, None


def date(entity: dict, prop: str) -> str | None:
    """ISO date at the precision Wikidata records (year, month or day)."""
    for statement in _ranked(entity, prop):
        value = _value(statement)
        if not isinstance(value, dict) or "time" not in value:
            continue
        raw = value["time"].lstrip("+")
        year, month, day = raw[:4], raw[5:7], raw[8:10]
        precision = value.get("precision", 11)
        if precision >= 11 and day != "00":
            return f"{year}-{month}-{day}"
        if precision >= 10 and month != "00":
            return f"{year}-{month}"
        return year
    return None


def quantity(entity: dict, prop: str) -> int | None:
    """Most recent quantity (by 'point in time' qualifier), e.g. employees."""
    best = None
    for statement in _ranked(entity, prop):
        value = _value(statement)
        if not isinstance(value, dict) or "amount" not in value:
            continue
        when = ""
        for qualifier in statement.get("qualifiers", {}).get("P585", []):
            when = qualifier.get("datavalue", {}).get("value", {}).get("time", "") or when
        candidate = (when, int(float(value["amount"])))
        best = candidate if best is None or candidate[0] > best[0] else best
    return best[1] if best else None


def label(entity: dict, language: str = "en") -> str | None:
    labels = entity.get("labels", {})
    if language in labels:
        return labels[language]["value"]
    return None


def any_label(entity: dict) -> str | None:
    for language in ("en", "ur"):
        if label(entity, language):
            return label(entity, language)
    labels = entity.get("labels", {})
    return next(iter(labels.values()))["value"] if labels else None


def aliases(entity: dict) -> list[str]:
    seen, result = set(), []
    for language in ("en", "ur"):
        for alias in entity.get("aliases", {}).get(language, []):
            if alias["value"] not in seen:
                seen.add(alias["value"])
                result.append(alias["value"])
    return result


def identifiers(entity: dict) -> dict[str, str]:
    found = {}
    for scheme, prop in IDENTIFIERS.items():
        value = first_string(entity, prop)
        if value:
            found[scheme] = value
    return found


def social_links(ids: dict[str, str]) -> list[str] | None:
    links = [template.format(ids[scheme]) for scheme, template in SOCIAL_URLS.items() if ids.get(scheme)]
    return links or None


def modified(entity: dict) -> datetime | None:
    value = entity.get("modified")
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).replace(microsecond=0)


def referenced_items(entity: dict) -> set[str]:
    found = set()
    for prop in ITEM_PROPERTIES:
        found.update(item_ids(entity, prop))
    return found
