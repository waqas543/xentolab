"""gold.domain_profiles: one profile per web domain, merged from every source's facts.

Inputs (inside the GOLD_BUILD DuckDB session):
  * silver.domain_facts: what any source observed about a domain (company websites via the scraping engine, ...)
  * silver.organizations: registry / knowledge-base records that state a website (Wikidata, OpenCorporates);
    they become domain facts with method "record", so those sources need nothing extra.

Each profile field takes the first method in its precedence list that has a value (see DOMAIN_FACT_METHODS for
what the methods mean). Within that method the most frequent value wins; ties go to the home page, then to the
higher-priority source. City is resolved against the country's gazetteer (CityIndex): the most populous known place
mentioned wins, so "4K Model Town, Lahore" gives Lahore. `field_sources` records "source:method" per field.
"""

from __future__ import annotations

import itertools
import json
import re
from collections import Counter
from datetime import date

import pyarrow as pa

from ..normalize import social_profile
from ..silver_contract import DOMAIN_FACT_SOCIAL_NETWORKS
from ..taxonomy import GROUPS

MAX_CONTACTS = 5
EVIDENCE_ORDER = {"home": 0, "about": 1, "company": 2, "contact": 3, "team": 4, "record": 5, "domain": 6}

# profile field -> (fact fields that can supply it, methods in precedence order)
PRECEDENCE: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "company_name": (("company_name",), ("site", "jsonld", "record", "meta", "llm")),
    "summary": (("summary", "description"), ("site", "llm", "jsonld", "meta", "record")),
    "industry": (("industry",), ("site", "llm", "record")),
    "category_group": (("category_group",), ("site", "llm", "record")),
    "city": (("city", "address"), ("site", "jsonld", "record", "llm")),
    "founded_year": (("founded_year",), ("site", "jsonld", "record", "llm")),
    "employees": (("employees",), ("site", "jsonld", "record", "llm")),
    "market_cap": (("market_cap",), ("site", "record", "llm")),
    "revenue": (("revenue",), ("site", "record", "llm")),
    "financial_info": (("financial_info",), ("site", "record", "llm")),
}
SOCIAL_METHODS = ("site", "jsonld", "record")       # stated profiles; otherwise the most linked one
CONTACT_METHODS = ("site", "jsonld", "record", "link", "text")

PROFILE_SCHEMA = pa.schema([
    ("domain", pa.string()), ("country_code", pa.string()), ("organization_id", pa.string()),
    ("company_name", pa.string()), ("summary", pa.string()), ("industry", pa.string()),
    ("category_group", pa.string()), ("city", pa.string()), ("founded_year", pa.int32()),
    ("employees", pa.string()), ("market_cap", pa.string()), ("revenue", pa.string()),
    ("financial_info", pa.string()),
    *((network, pa.string()) for network in DOMAIN_FACT_SOCIAL_NETWORKS),
    ("emails", pa.list_(pa.string())), ("phones", pa.list_(pa.string())),
    ("sources", pa.list_(pa.string())), ("pages_crawled", pa.int32()), ("fact_count", pa.int32()),
    ("field_sources", pa.string()), ("observed_at", pa.timestamp("s", tz="UTC")),
])

_TAGLINE = re.compile(r"\s+[|–—-]\s+|\s*\|\s*")
_DOMAIN_LIKE = re.compile(r"^(www\.)?[\w-]+(\.[\w-]+)+$", re.I)
_YEAR = re.compile(r"\b(1[89]\d\d|20\d\d)\b")


def clean_name(value: str) -> str | None:
    """'Marham - Find A Doctor' -> 'Marham'; 'www.marham.pk' -> None (a domain is not a name)."""
    name = _TAGLINE.split(value.strip())[0].strip()
    return None if not name or _DOMAIN_LIKE.match(name) else name[:200]


class CityIndex:
    """Known city/town names of a country, most populous first: finds the main place named in an address."""

    def __init__(self, names_by_population: list[str]):
        self.rank = {}
        for name in names_by_population:
            self.rank.setdefault(name.casefold(), (len(self.rank), name))
        names = sorted({n for _, n in self.rank.values()}, key=len, reverse=True)
        self._pattern = re.compile(r"(?<!\w)(" + "|".join(map(re.escape, names)) + r")(?!\w)", re.I) if names else None

    def __bool__(self) -> bool:
        return self._pattern is not None

    def match(self, text: str | None) -> str | None:
        """The most populous known place named in `text` ('4K Model Town, Lahore' -> 'Lahore')."""
        if not text or self._pattern is None:
            return None
        found = [self.rank[m.casefold()] for m in self._pattern.findall(text) if m.casefold() in self.rank]
        return min(found)[1] if found else None

    def order(self, name: str) -> int:
        return self.rank.get(name.casefold(), (1 << 30, name))[0]


def _clean(field: str, fact_field: str, value, cities: CityIndex):
    if value in (None, ""):
        return None
    value = str(value).strip()
    if field == "city":
        if cities:
            return cities.match(value)
        return value if fact_field == "city" else None      # no gazetteer: keep explicit city facts only
    if field == "founded_year":
        year = _YEAR.search(value)
        return int(year.group(1)) if year and int(year.group(1)) <= date.today().year else None
    if field == "company_name":
        return clean_name(value)
    if field == "category_group":
        return value if value in GROUPS else None
    if field == "summary":
        return value[:1000]
    return value[:500] or None


def build_profile(domain: str, country: str, facts: list[dict], *, cities: CityIndex,
                  source_priority: dict[str, int], organization_id: str | None = None,
                  fallback_group: str | None = None) -> dict:
    """Merge one domain's facts ({field, value, source, method, evidence_type, evidence_url}) into a profile."""
    ordered = sorted(facts, key=lambda f: (EVIDENCE_ORDER.get(f.get("evidence_type") or "", 9),
                                           -source_priority.get(f["source"], 10)))
    by_method: dict[tuple[str, str], list[dict]] = {}
    for fact in ordered:
        if fact["field"] == "social":                        # record-style raw profile URL: classify it
            profile = social_profile(fact["value"])
            if not profile:
                continue
            fact = {**fact, "field": f"social.{profile[0]}", "value": profile[1]}
        by_method.setdefault((fact["field"], fact["method"]), []).append(fact)

    profile: dict = {"domain": domain, "country_code": country, "organization_id": organization_id}
    sources: dict[str, str] = {}
    for field, (fact_fields, methods) in PRECEDENCE.items():
        profile[field] = None
        for method in methods:
            candidates = [(value, fact) for fact_field in fact_fields for fact in by_method.get((fact_field, method), [])
                          if (value := _clean(field, fact_field, fact["value"], cities)) is not None]
            if not candidates:
                continue
            if field == "city":
                value = min((v for v, _ in candidates), key=cities.order) if cities else candidates[0][0]
            else:
                value = Counter(v for v, _ in candidates).most_common(1)[0][0]
            winner = next(fact for v, fact in candidates if v == value)
            profile[field], sources[field] = value, f"{winner['source']}:{method}"
            break
    if profile["category_group"] is None and fallback_group in GROUPS:
        profile["category_group"], sources["category_group"] = fallback_group, "gold:businesses"

    for network in DOMAIN_FACT_SOCIAL_NETWORKS:
        field = f"social.{network}"
        stated = next((by_method[(field, m)][0] for m in SOCIAL_METHODS if by_method.get((field, m))), None)
        if stated is None and (linked := by_method.get((field, "link"))):
            top = Counter(f["value"] for f in linked).most_common(1)[0][0]
            stated = next(f for f in linked if f["value"] == top)
        profile[network] = stated["value"] if stated else None
        if stated:
            sources[network] = f"{stated['source']}:{stated['method']}"

    for field, column in (("email", "emails"), ("phone", "phones")):
        values: list[str] = []
        for method in CONTACT_METHODS:
            for fact in by_method.get((field, method), []):
                if fact["value"] not in values:
                    values.append(fact["value"])
        profile[column] = values[:MAX_CONTACTS] or None

    profile["sources"] = sorted({f["source"] for f in facts})
    profile["pages_crawled"] = len({f["evidence_url"] for f in facts if f.get("evidence_url")
                                    and f.get("evidence_type") not in ("record", "domain")})
    profile["fact_count"] = len(facts)
    profile["field_sources"] = json.dumps(sources, sort_keys=True)
    profile["observed_at"] = max((f["observed_at"] for f in facts if f.get("observed_at")), default=None)
    return profile


# ---------------------------------------------------------------- inside GOLD_BUILD

def facts_sql(country: str) -> str:
    """All domain facts for the country: silver.domain_facts plus organization records that state a website.

    Expects the views `domain_facts` (silver.domain_facts) and `orgrec` (silver.organizations, country-filtered).
    """
    record = """SELECT normalized_domain AS domain, '{field}' AS field, {value} AS value, source, 'record' AS method,
                       'record' AS evidence_type, source_url AS evidence_url, ingested_at AS observed_at
                FROM orgrec_sites WHERE {value} IS NOT NULL"""
    parts = [record.format(field=field, value=value) for field, value in (
        ("company_name", "name"), ("description", "description"), ("industry", "industries[1]"),
        ("category_group", "category_group"), ("city", "headquarters"), ("address", "address"),
        ("founded_year", "inception"), ("employees", "CAST(employees AS VARCHAR)"), ("email", "email"),
        ("phone", "normalized_phone"))]
    parts.append("""SELECT normalized_domain, 'social', link, source, 'record', 'record', source_url, ingested_at
                    FROM orgrec_sites, unnest(social_links) AS s(link) WHERE link IS NOT NULL""")
    return f"""
        WITH orgrec_sites AS (
            SELECT * FROM orgrec WHERE normalized_domain IS NOT NULL AND NOT coalesce(domain_is_platform, false)
        )
        SELECT domain, field, value, source, method, evidence_type, evidence_url, observed_at FROM domain_facts
        WHERE (country_code = '{country}' OR country_code IS NULL) AND value IS NOT NULL AND value <> ''
        UNION ALL
        {" UNION ALL ".join(parts)}
        ORDER BY 1
    """


def build_table(con, country: str, source_priority: dict[str, int]) -> pa.Table:
    """Profiles for every domain with at least one fact. Needs the `domains` and `geo_places` views too."""
    cities = CityIndex([name for (name,) in con.execute("""
        SELECT name FROM (SELECT name, max(coalesce(population, 0)) AS population FROM geo_places
                          WHERE kind IN ('city', 'town') AND name IS NOT NULL GROUP BY name)
        ORDER BY population DESC, name
    """).fetchall()])
    linked = {domain: (organization_id, group) for domain, organization_id, group in con.execute(
        "SELECT domain, organization_id, top_category_group FROM gold_domains").fetchall()}
    cursor = con.execute(facts_sql(country))
    columns = [d[0] for d in cursor.description]
    rows: list[dict] = []
    while batch := cursor.fetchmany(50_000):
        rows.extend(dict(zip(columns, row)) for row in batch)
    profiles = []
    for domain, facts in itertools.groupby(rows, key=lambda r: r["domain"]):
        organization_id, group = linked.get(domain, (None, None))
        profiles.append(build_profile(domain, country, list(facts), cities=cities, source_priority=source_priority,
                                      organization_id=organization_id, fallback_group=group))
    return pa.Table.from_pylist(profiles, schema=PROFILE_SCHEMA)
