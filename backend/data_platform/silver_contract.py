"""The shared Silver contract: schemas every source writes, plus shared geography loading.

Adapters normalize their own Bronze into these schemas. Entity resolution, Gold and
the APIs only depend on this contract, which is what keeps sources pluggable.
"""

from __future__ import annotations

import pyarrow as pa
import shapely

from . import repository
from .geo import Boundary, GeoIndex, Place
from .lake.reader import dataset_files, duck, parquet_source

CONTRACT_VERSION = "silver-contract-3"   # 3: places carry city_id and location_precision
# 2: social_links, silver.geo_places

# Which source's populated places define city / locality when several sources provide them.
# GeoNames is a complete gazetteer (every village, with population); OSM places are the fallback.
GEO_PLACE_PRECEDENCE = ["geonames", "osm"]

PLACES_SCHEMA = pa.schema([
    ("silver_id", pa.string()), ("source", pa.string()), ("source_record_id", pa.string()),
    ("source_entity_type", pa.string()),
    ("name", pa.string()), ("name_local", pa.string()), ("normalized_name", pa.string()), ("name_key", pa.string()),
    ("has_name", pa.bool_()),
    ("entity_type", pa.string()), ("category_key", pa.string()), ("category", pa.string()),
    ("category_group", pa.string()), ("subcategory", pa.string()),
    ("brand_name", pa.string()), ("brand_wikidata", pa.string()), ("operator", pa.string()), ("wikidata", pa.string()),
    ("country_code", pa.string()), ("admin1", pa.string()), ("admin2", pa.string()), ("city", pa.string()),
    ("locality", pa.string()), ("locality_distance_km", pa.float64()),
    ("city_id", pa.string()),               # gazetteer place id of `city`: same-named towns stay apart
    # exact | approximate. Approximate = the provider placed the record on a town / city centre or stacked many
    # unrelated records on one point; it is kept, but not used for maps, distances or point-based geography.
    ("location_precision", pa.string()),
    ("postal_code", pa.string()), ("street", pa.string()), ("housenumber", pa.string()), ("address", pa.string()),
    ("latitude", pa.float64()), ("longitude", pa.float64()),
    ("website", pa.string()), ("normalized_domain", pa.string()), ("tld", pa.string()),
    ("domain_is_platform", pa.bool_()),
    ("phone", pa.string()), ("normalized_phone", pa.string()), ("email", pa.string()),
    ("social_links", pa.list_(pa.string())),
    ("opening_hours", pa.string()),
    ("source_confidence", pa.float64()),
    ("source_updated_at", pa.timestamp("s", tz="UTC")), ("source_version", pa.string()),
    ("artifact_id", pa.int64()), ("bronze_version_id", pa.int64()), ("pipeline_run_id", pa.int64()),
    ("transformation_version", pa.string()), ("ingested_at", pa.timestamp("s", tz="UTC")),
    ("attributes", pa.string()),
])

REJECTS_SCHEMA = pa.schema([
    ("source", pa.string()), ("source_record_id", pa.string()), ("reason", pa.string()), ("name", pa.string()),
    ("latitude", pa.float64()), ("longitude", pa.float64()), ("attributes", pa.string()),
    ("artifact_id", pa.int64()), ("pipeline_run_id", pa.int64()), ("rejected_at", pa.timestamp("s", tz="UTC")),
])

BOUNDARIES_SCHEMA = pa.schema([
    ("boundary_id", pa.string()), ("source", pa.string()), ("admin_level", pa.int16()), ("name", pa.string()),
    ("name_local", pa.string()), ("country_code", pa.string()), ("area_km2", pa.float64()),
    ("geometry_wkb", pa.binary()), ("artifact_id", pa.int64()), ("pipeline_run_id", pa.int64()),
])

GEO_PLACES_SCHEMA = pa.schema([
    ("place_id", pa.string()), ("source", pa.string()), ("name", pa.string()), ("kind", pa.string()),
    ("latitude", pa.float64()), ("longitude", pa.float64()), ("population", pa.int64()),
    ("artifact_id", pa.int64()), ("pipeline_run_id", pa.int64()),
    # Gazetteer detail (GeoNames); empty for sources that do not provide it.
    ("name_local", pa.string()), ("alternate_names", pa.list_(pa.string())), ("feature_code", pa.string()),
    ("country_code", pa.string()), ("admin1", pa.string()), ("admin2", pa.string()), ("timezone", pa.string()),
    ("elevation_m", pa.int32()), ("source_updated_at", pa.date32()),
])

POSTAL_CODES_SCHEMA = pa.schema([
    ("postal_code", pa.string()), ("source", pa.string()), ("country_code", pa.string()), ("place_name", pa.string()),
    ("admin1", pa.string()), ("admin2", pa.string()), ("admin3", pa.string()),
    ("latitude", pa.float64()), ("longitude", pa.float64()), ("accuracy", pa.int16()),
    ("artifact_id", pa.int64()), ("pipeline_run_id", pa.int64()),
])

# Organizations (companies, brands, institutions) as described by registries and knowledge bases.
# Not locations: entity resolution links them to silver.places, and Gold folds them into gold.organizations.
ORGANIZATIONS_SCHEMA = pa.schema([
    ("silver_id", pa.string()), ("source", pa.string()), ("source_record_id", pa.string()),
    ("name", pa.string()), ("name_local", pa.string()), ("legal_name", pa.string()),
    ("normalized_name", pa.string()), ("name_key", pa.string()),
    ("aliases", pa.list_(pa.string())), ("alias_keys", pa.list_(pa.string())),
    ("description", pa.string()), ("entity_type", pa.string()),
    ("org_types", pa.list_(pa.string())), ("industries", pa.list_(pa.string())), ("category_group", pa.string()),
    ("country_code", pa.string()), ("jurisdiction", pa.string()),
    ("headquarters", pa.string()), ("latitude", pa.float64()), ("longitude", pa.float64()),
    ("address", pa.string()),
    ("website", pa.string()), ("normalized_domain", pa.string()), ("tld", pa.string()),
    ("domain_is_platform", pa.bool_()),
    ("email", pa.string()), ("phone", pa.string()), ("normalized_phone", pa.string()),
    ("social_links", pa.list_(pa.string())),
    ("inception", pa.string()), ("dissolved", pa.string()), ("status", pa.string()), ("legal_form", pa.string()),
    ("registry_id", pa.string()),
    ("parent_id", pa.string()), ("parent_name", pa.string()),
    ("subsidiaries", pa.list_(pa.string())), ("founders", pa.list_(pa.string())), ("brands", pa.list_(pa.string())),
    ("employees", pa.int64()),
    ("wikidata", pa.string()), ("identifiers", pa.string()),       # JSON object: scheme -> value
    ("source_url", pa.string()),
    ("source_confidence", pa.float64()),
    ("source_updated_at", pa.timestamp("s", tz="UTC")), ("source_version", pa.string()),
    ("artifact_id", pa.int64()), ("bronze_version_id", pa.int64()), ("pipeline_run_id", pa.int64()),
    ("transformation_version", pa.string()), ("ingested_at", pa.timestamp("s", tz="UTC")),
    ("attributes", pa.string()),
])

# Registrable web domains seen by a crawler (domain discovery), one row per domain.
WEB_DOMAINS_SCHEMA = pa.schema([
    ("domain", pa.string()), ("source", pa.string()), ("country_code", pa.string()),
    ("tld", pa.string()), ("suffix", pa.string()), ("domain_type", pa.string()),
    ("hosts", pa.list_(pa.string())), ("host_count", pa.int32()),
    ("captures", pa.int64()), ("pages", pa.int64()),
    ("first_seen", pa.timestamp("s", tz="UTC")), ("last_seen", pa.timestamp("s", tz="UTC")),
    ("status_2xx", pa.int64()), ("status_3xx", pa.int64()), ("status_4xx", pa.int64()), ("status_5xx", pa.int64()),
    ("ok_ratio", pa.float64()), ("languages", pa.list_(pa.string())), ("top_language", pa.string()),
    ("homepage_url", pa.string()), ("homepage_status", pa.int32()), ("redirect_domain", pa.string()),
    # Where the homepage capture lives, so its WARC record can be fetched later (page-content extraction).
    ("warc_filename", pa.string()), ("warc_offset", pa.int64()), ("warc_length", pa.int64()),
    ("crawl", pa.string()), ("is_business_candidate", pa.bool_()),
    ("capture_coverage", pa.string()),      # complete | sample (large known sites: first index block + homepage)
    ("artifact_id", pa.int64()), ("bronze_version_id", pa.int64()), ("pipeline_run_id", pa.int64()),
    ("transformation_version", pa.string()), ("ingested_at", pa.timestamp("s", tz="UTC")),
])

# Facts about a web domain (the company behind it), one row per (domain, field, value) observation.
# Any source can contribute in this shape: the company's own website (via the scraping engine), a stock
# exchange listing, a business directory... Gold merges them into gold.domain_profiles by field precedence.
DOMAIN_FACTS_SCHEMA = pa.schema([
    ("domain", pa.string()), ("country_code", pa.string()),
    ("field", pa.string()),                 # one of DOMAIN_FACT_FIELDS
    ("value", pa.string()),                 # normalized (E.164 phones, lower-case emails, canonical profile URLs)
    ("source", pa.string()),                # provider: website, psx, ...
    ("method", pa.string()),                # one of DOMAIN_FACT_METHODS: how the value was obtained
    ("evidence", pa.string()),              # the quote / raw value it came from, when there is one
    ("evidence_url", pa.string()),          # the page or record it was read from
    ("evidence_type", pa.string()),         # home | about | contact | company | team | record | domain
    ("confidence", pa.float64()),
    ("observed_at", pa.timestamp("s", tz="UTC")),
    ("source_record_id", pa.string()),
    ("artifact_id", pa.int64()), ("bronze_version_id", pa.int64()), ("pipeline_run_id", pa.int64()),
    ("transformation_version", pa.string()), ("ingested_at", pa.timestamp("s", tz="UTC")),
])

DOMAIN_FACT_SOCIAL_NETWORKS = ("facebook", "instagram", "x_twitter", "linkedin", "youtube", "tiktok", "pinterest",
                               "whatsapp", "threads")
DOMAIN_FACT_FIELDS = frozenset({
    "company_name", "description", "summary", "industry", "category_group", "city", "address", "founded_year",
    "employees", "market_cap", "revenue", "financial_info", "email", "phone",
    *(f"social.{network}" for network in DOMAIN_FACT_SOCIAL_NETWORKS),
})
# How a value was obtained, from most to least deliberate. Gold precedence is expressed in these terms, so a
# new source only has to say which method each of its facts came from.
DOMAIN_FACT_METHODS = (
    "site",      # a custom parser written for that one site
    "jsonld",    # schema.org markup the company published on its site
    "record",    # a structured record from a registry, exchange or knowledge base (Wikidata, OpenCorporates, PSX)
    "meta",      # HTML meta / Open Graph tags
    "link",      # hyperlinks on the site: social profiles, mailto:, tel:
    "text",      # patterns found in page text
    "llm",       # language-model extraction (quoted evidence verified against the text)
)

# Organizations mentioned in news, aggregated per normalized name (a discovery / popularity signal).
ORG_MENTIONS_SCHEMA = pa.schema([
    ("name_key", pa.string()), ("source", pa.string()), ("country_code", pa.string()),
    ("name", pa.string()), ("surface_forms", pa.list_(pa.string())),
    ("mentions", pa.int64()), ("articles", pa.int64()), ("outlets", pa.int64()),
    ("first_seen", pa.timestamp("s", tz="UTC")), ("last_seen", pa.timestamp("s", tz="UTC")),
    ("avg_tone", pa.float64()), ("top_themes", pa.list_(pa.string())), ("top_locations", pa.list_(pa.string())),
    ("sample_urls", pa.list_(pa.string())), ("days_covered", pa.int32()),
    ("pipeline_run_id", pa.int64()), ("transformation_version", pa.string()),
    ("ingested_at", pa.timestamp("s", tz="UTC")),
])


def empty_columns(schema: pa.Schema) -> dict[str, list]:
    return {field.name: [] for field in schema}


def to_table(columns: dict[str, list], schema: pa.Schema) -> pa.Table:
    """Build a table for `schema`; columns a source does not provide are filled with nulls."""
    n = len(next(iter(columns.values()))) if columns else 0
    return pa.table({f.name: columns.get(f.name, [None] * n) for f in schema}, schema=schema)


def geography_versions(country: str) -> tuple[list[dict], list[dict]]:
    """(boundary versions, populated-place versions) that define the shared geography for a country.

    All boundary sources are used; populated places come from the highest-precedence source only,
    so two gazetteers never compete over the same town.
    """
    boundary_versions = repository.current_versions("silver.geo_boundaries", {"country": country})
    place_versions = repository.current_versions("silver.geo_places", {"country": country})
    by_source = {v["partition"].get("source"): v for v in place_versions if v["row_count"]}
    chosen = next((by_source[s] for s in GEO_PLACE_PRECEDENCE if s in by_source), None)
    if chosen is None and by_source:
        chosen = by_source[sorted(by_source)[0]]
    return boundary_versions, [chosen] if chosen else []


def geography_version_ids(country: str) -> list[int]:
    boundaries, places = geography_versions(country)
    return sorted(v["id"] for v in boundaries + places)


def load_geo_index(country: str) -> tuple[GeoIndex | None, list[int]]:
    """Geography from the current Silver boundaries/places for a country, whichever source produced them.

    Returns (index or None when no geography exists yet, the dataset version ids used — for lineage).
    """
    boundary_versions, place_versions = geography_versions(country)
    if not boundary_versions and not place_versions:
        return None, []
    boundaries: list[Boundary] = []
    places: list[Place] = []
    con = duck()
    try:
        files = dataset_files("silver.geo_boundaries", versions=boundary_versions)
        if files:
            for level, name, code, wkb in con.execute(
                    f"SELECT admin_level, name, country_code, geometry_wkb FROM {parquet_source(files)}").fetchall():
                boundaries.append(Boundary(int(level), name, code, shapely.from_wkb(wkb)))
        files = dataset_files("silver.geo_places", versions=place_versions)
        if files:
            source = parquet_source(files)
            columns = {r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {source}").fetchall()}
            admin = ("country_code, admin1, admin2" if "admin1" in columns
                     else "NULL AS country_code, NULL AS admin1, NULL AS admin2")
            populations = best_populations(con, country)
            for place_id, name, kind, lat, lon, population, code, admin1, admin2 in con.execute(
                    f"SELECT place_id, name, kind, latitude, longitude, population, {admin} FROM {source}").fetchall():
                if kind in ("city", "town"):
                    population = max(population or 0, populations.get((name.casefold(), round(lat), round(lon)), 0)) or None
                places.append(Place(name, kind, lat, lon, population, code, admin1, admin2, place_id))
    finally:
        con.close()
    return GeoIndex(boundaries, places), sorted(v["id"] for v in boundary_versions + place_versions)


def best_populations(con, country: str) -> dict[tuple[str, int, int], int]:
    """Largest population any gazetteer gives a city or town, keyed by (name, rounded lat, rounded lon).

    Gazetteers lag censuses unevenly (GeoNames has Islamabad at 601,600 while OSM has 1,009,832); cities only
    grow, so the larger figure is the more recent one. Population sets how far a city reaches.
    """
    versions = repository.current_versions("silver.geo_places", {"country": country})
    files = dataset_files("silver.geo_places", versions=versions)
    if not files:
        return {}
    rows = con.execute(f"""
        SELECT lower(name), latitude, longitude, population FROM {parquet_source(files)}
        WHERE kind IN ('city', 'town') AND population > 0
    """).fetchall()
    best: dict[tuple[str, int, int], int] = {}
    for name, lat, lon, population in rows:
        for dlat in (-1, 0, 1):                      # neighbouring 1-degree cells: sources disagree by a few km
            for dlon in (-1, 0, 1):
                key = (name.casefold(), round(lat) + dlat, round(lon) + dlon)
                best[key] = max(best.get(key, 0), int(population))
    return best


# ---------------------------------------------------------------- location precision

# Providers geocode records they cannot place to a town or city centre (Overture stacks 1,700 Karachi listings on
# one point; Google put 757 on Pakistan's centroid). Such points are kept but flagged, and their geography comes
# from the city the provider states instead of from the point.
STACKED_MIN_RECORDS = 10        # this many records of one source on the identical point (~1 m) = a geocoding default
ON_PLACE_CENTRE_KM = 0.02       # a point this close to a gazetteer place centre, shared with another record


def stacked_points_sql(table: str) -> str:
    """`table` with stack_size: how many records of the same table sit on the identical point."""
    return f"""
        SELECT *, count(*) OVER (PARTITION BY round(latitude, 5), round(longitude, 5)) AS stack_size
        FROM {table}
    """


def location_precision(stack_size: int | None, locality_distance_km: float | None) -> str:
    stack = stack_size or 1
    if stack >= STACKED_MIN_RECORDS:
        return "approximate"
    if stack >= 2 and locality_distance_km is not None and locality_distance_km <= ON_PLACE_CENTRE_KM:
        return "approximate"
    return "exact"


def place_geography(geo: GeoIndex | None, geo_values: dict | None, index: int, *, precision: str,
                    stated_city: str | None, stated_admin1: str | None = None) -> dict:
    """admin1 / admin2 / city / city_id / locality for one record, honouring its location precision.

    Exact points take the geography of the point. Approximate points take the city the provider states when the
    gazetteer knows it; otherwise they keep the point's city only if a real city reaches the point (a city-centre
    default), and are left without city and province when the point is merely the nearest village (a country
    centroid).
    """
    if geo_values is None:
        return {"admin1": stated_admin1, "admin2": None, "city": stated_city, "city_id": None,
                "locality": None, "locality_distance_km": None}
    point = {key: geo_values[key][index] for key in
             ("admin1", "admin2", "city", "city_id", "locality", "locality_distance_km")}
    if precision == "exact":
        return point
    stated = geo.city_by_name(stated_city) if geo else None
    if stated:
        return {"admin1": stated["admin1"], "admin2": stated["admin2"], "city": stated["city"],
                "city_id": stated["city_id"], "locality": None, "locality_distance_km": None}
    if geo_values["city_match"][index] in ("city", "territory"):
        return {**point, "locality": None, "locality_distance_km": None}
    return {"admin1": None, "admin2": None, "city": None, "city_id": None, "locality": None,
            "locality_distance_km": None}
