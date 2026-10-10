"""GOLD_BUILD stage: resolved businesses, organizations, domains and cities for one country, with provenance.

Clusters = connected components of `automatic` + `accepted` location matches (union-find);
unmatched records are singleton businesses. Survivorship picks each attribute from
the best member: higher source priority, then more complete records. Gold IDs are
derived from the cluster's anchor record, so they are stable across rebuilds while
cluster membership is unchanged.

Non-location sources enrich rather than create businesses: organization records
(Wikidata, registries) form gold.organizations (see organizations.py), crawled web
domains extend gold.domains, news mentions add signals, and the gazetteer gives gold.cities.
"""

from __future__ import annotations

import hashlib

import pyarrow as pa

from .. import db, repository
from ..engine.context import StageContext
from ..errors import PermanentError
from ..lake.reader import dataset_files, duck, parquet_source
from ..lake.writer import DatasetVersionWriter, copy_query_into
from ..normalize import name_key
from ..resolution.service import er_fingerprint, er_input_ids, named_places_view
from ..silver_contract import (
    GEO_PLACES_SCHEMA,
    ORG_MENTIONS_SCHEMA,
    ORGANIZATIONS_SCHEMA,
    WEB_DOMAINS_SCHEMA,
    geography_versions,
)
from ..sources.base import StageResult
from . import organizations
from .organizations import UnionFind
from .version import GOLD_VERSION

# Higher wins in survivorship. New sources slot in here (e.g. a government registry above OSM).
# OSM ranks above Overture: its names and positions are community-curated, while Overture's places
# come largely from social-media pages. Attributes a higher-ranked member lacks are still filled from others.
SOURCE_PRIORITY = {"secp": 100, "opencorporates": 90, "wikidata": 80, "osm": 60, "overture": 50, "geonames": 40}

# Clusters whose every member has a provider confidence below this are not promoted to Gold
# (they stay in Silver). Sources without per-record confidence (OSM) count as fully confident.
MIN_GOLD_CONFIDENCE = 0.3

GOLD_DATASETS = ("gold.businesses", "gold.business_sources", "gold.organizations", "gold.organization_sources",
                 "gold.domains", "gold.cities")
# Silver inputs besides silver.places: (dataset, empty schema used when a country has none yet).
ENRICHMENT_INPUTS = {"silver.organizations": ("orgrec", ORGANIZATIONS_SCHEMA),
                     "silver.web_domains": ("web_domains", WEB_DOMAINS_SCHEMA),
                     "silver.org_mentions": ("mentions", ORG_MENTIONS_SCHEMA)}


def gold_id_for(anchor: str) -> str:
    return "gb_" + hashlib.sha256(anchor.encode()).hexdigest()[:16]


def load_clusters(country: str) -> tuple[dict[str, str], dict[str, float]]:
    """silver_id -> anchor silver_id for matched records, and best linking confidence per record."""
    with db.serving() as conn:
        rows = conn.execute("""
            SELECT left_id, right_id, match_score FROM er.entity_matches
            WHERE country_code = %s AND match_type = 'location' AND status IN ('automatic', 'accepted')
        """, [country]).fetchall()
    union = UnionFind()
    confidence: dict[str, float] = {}
    for row in rows:
        union.union(row["left_id"], row["right_id"])
        for side in ("left_id", "right_id"):
            confidence[row[side]] = max(confidence.get(row[side], 0.0), row["match_score"])
    return {item: union.find(item) for item in union.parent}, confidence


def build_gold(ctx: StageContext) -> StageResult:
    country = ctx.params["country"]
    silver = repository.current_versions("silver.places", {"country": country})
    if not silver:
        return StageResult.skipped(f"No Silver places for {country}")
    er_state = repository.get_checkpoint(f"er:{country}", "state")
    er_ids = er_input_ids(country)
    if not er_state or er_state.get("silver_versions") != er_ids:
        # Another source's run changed Silver for this country since this run resolved: resolve again first.
        from ..resolution.service import resolve

        resolve(ctx)
        er_state = repository.get_checkpoint(f"er:{country}", "state")
        if not er_state or er_state.get("silver_versions") != er_ids:
            raise PermanentError("Entity resolution could not be brought up to date with Silver")

    enrichment = {d: repository.current_versions(d, {"country": country}) for d in ENRICHMENT_INPUTS}
    _, geo_places = geography_versions(country)
    fingerprint = er_fingerprint(country)
    versions = {"gold": GOLD_VERSION, "entity_resolution": er_state["algorithm"],
                "organization_linking": er_state.get("organization_algorithm"), "er_fingerprint": fingerprint}
    input_versions = [("silver.places", v) for v in silver] + [
        (d, v) for d, vs in enrichment.items() for v in vs] + [("silver.geo_places", v) for v in geo_places]
    inputs = [{"type": "dataset_version", "dataset_id": d, "id": v["id"]} for d, v in input_versions] + [
        {"type": "entity_resolution", "algorithm": er_state["algorithm"], "fingerprint": fingerprint}]
    input_ids = sorted(v["id"] for _, v in input_versions)

    current = {d: repository.current_versions(d, {"country": country}) for d in GOLD_DATASETS}
    if all(current.values()) and not ctx.params["rebuild_gold"]:
        existing = current["gold.businesses"][0]
        built_from = sorted(i["id"] for i in existing["inputs"] if i.get("type") == "dataset_version")
        if built_from == input_ids and existing["versions"] == versions:
            ctx.put(gold_versions={d: v[0]["id"] for d, v in current.items()})
            return StageResult.skipped("Gold is current for these Silver versions and match decisions",
                                       records_out=existing["row_count"])

    clusters, confidence = load_clusters(country)
    silver_version_by_source = {v["partition"]["source"]: v["id"] for v in silver}
    priorities = "CASE source " + " ".join(f"WHEN '{s}' THEN {p}" for s, p in SOURCE_PRIORITY.items()) + " ELSE 10 END"

    con = duck()
    try:
        named_places_view(con, dataset_files("silver.places", versions=silver), country)
        con.register("cluster_map", pa.table({
            "silver_id": list(clusters), "anchor": list(clusters.values()),
            "link_confidence": [confidence[k] for k in clusters],
        }))
        con.register("silver_versions", pa.table({
            "source": list(silver_version_by_source), "silver_version_id": list(silver_version_by_source.values()),
        }))
        con.create_function("name_key", name_key, ["VARCHAR"], "VARCHAR", null_handling="special")
        for dataset_id, (view, schema) in ENRICHMENT_INPUTS.items():
            files = dataset_files(dataset_id, versions=enrichment[dataset_id])
            if files:
                con.execute(f"CREATE VIEW {view} AS SELECT * FROM {parquet_source(files)}"
                            + (f" WHERE name IS NOT NULL AND (country_code = '{country}' OR country_code IS NULL)"
                               if view == "orgrec" else ""))
            else:
                con.register(view, schema.empty_table())
        excluded_unnamed = con.execute(f"""
            SELECT count(*) FROM {parquet_source(dataset_files('silver.places', versions=silver))}
            WHERE NOT has_name AND (country_code = '{country}' OR country_code IS NULL)
        """).fetchone()[0]

        con.execute(f"""
            CREATE TEMP TABLE members AS
            WITH scored AS (
                SELECT p.*, coalesce(c.anchor, p.silver_id) AS anchor,
                       coalesce(c.link_confidence, 1.0) AS link_confidence,
                       'gb_' || substr(sha256(coalesce(c.anchor, p.silver_id)), 1, 16) AS gold_id,
                       {priorities} * 10
                         + (normalized_domain IS NOT NULL AND NOT coalesce(domain_is_platform, false))::INT * 3
                         + (normalized_phone IS NOT NULL)::INT * 2 + (email IS NOT NULL)::INT
                         + (address IS NOT NULL)::INT AS rank_score
                FROM places p LEFT JOIN cluster_map c USING (silver_id)
            )
            SELECT *, row_number() OVER (PARTITION BY gold_id ORDER BY rank_score DESC, silver_id) AS member_rank
            FROM scored
        """)
        ctx.progress(0, None, "records", "applying survivorship rules", force=True)
        con.execute(f"""
            CREATE TEMP TABLE businesses AS
            WITH primary_member AS (SELECT * FROM members WHERE member_rank = 1),
            merged AS (
                SELECT gold_id,
                    arg_max(website, rank_score) FILTER (WHERE normalized_domain IS NOT NULL AND NOT domain_is_platform)
                        AS website_own,
                    arg_max(normalized_domain, rank_score) FILTER (WHERE normalized_domain IS NOT NULL AND NOT domain_is_platform)
                        AS domain,
                    arg_max(tld, rank_score) FILTER (WHERE normalized_domain IS NOT NULL AND NOT domain_is_platform) AS tld,
                    arg_max(website, rank_score) FILTER (WHERE website IS NOT NULL) AS website_any,
                    arg_max(normalized_phone, rank_score) FILTER (WHERE normalized_phone IS NOT NULL) AS phone,
                    arg_max(email, rank_score) FILTER (WHERE email IS NOT NULL) AS email,
                    arg_max(address, rank_score) FILTER (WHERE address IS NOT NULL) AS address,
                    arg_max(postal_code, rank_score) FILTER (WHERE postal_code IS NOT NULL) AS postal_code,
                    arg_max(brand_name, rank_score) FILTER (WHERE brand_name IS NOT NULL) AS brand_name,
                    arg_max(brand_wikidata, rank_score) FILTER (WHERE brand_wikidata IS NOT NULL) AS brand_wikidata,
                    arg_max(trim(split_part(wikidata, ';', 1)), rank_score) FILTER (WHERE wikidata IS NOT NULL)
                        AS wikidata_own,
                    arg_max(source, rank_score) FILTER (WHERE normalized_domain IS NOT NULL AND NOT domain_is_platform)
                        AS website_source,
                    arg_max(source, rank_score) FILTER (WHERE normalized_phone IS NOT NULL) AS phone_source,
                    arg_max(source, rank_score) FILTER (WHERE email IS NOT NULL) AS email_source,
                    arg_max(source, rank_score) FILTER (WHERE address IS NOT NULL) AS address_source,
                    max(coalesce(source_confidence, 1.0)) AS confidence,
                    list(DISTINCT source ORDER BY source) AS sources,
                    count(*) AS member_count,
                    max(source_updated_at) AS source_updated_at,
                    list_filter([
                        CASE WHEN count(DISTINCT normalized_phone) > 1 THEN 'phone' END,
                        CASE WHEN count(DISTINCT normalized_domain) FILTER (WHERE NOT domain_is_platform) > 1 THEN 'domain' END,
                        CASE WHEN count(DISTINCT category) > 1 THEN 'category' END,
                        CASE WHEN count(DISTINCT name_key) > 1 THEN 'name' END
                    ], x -> x IS NOT NULL) AS conflicts
                FROM members GROUP BY gold_id
            )
            SELECT p.gold_id, p.name, p.normalized_name, p.entity_type, p.category_group, p.category,
                   -- Gold rows belong to their partition. Silver keeps NULL for places outside every known
                   -- boundary (reported on the quality dashboard); Gold attributes them to the extract's country.
                   '{country}' AS country_code, p.admin1, p.admin2, p.city, p.locality, m.postal_code, m.address,
                   p.latitude, p.longitude, coalesce(m.website_own, m.website_any) AS website, m.domain, m.tld,
                   m.phone, m.email, m.brand_name, m.brand_wikidata,
                   CAST(NULL AS VARCHAR) AS organization_id,
                   m.sources, len(m.sources)::SMALLINT AS source_count, m.member_count::INTEGER AS member_count,
                   coalesce(m.website_own, m.website_any) IS NOT NULL AS has_website,
                   m.phone IS NOT NULL AS has_phone, m.email IS NOT NULL AS has_email,
                   m.address IS NOT NULL AS has_address,
                   (25 * ((coalesce(m.website_own, m.website_any) IS NOT NULL)::INT + (m.phone IS NOT NULL)::INT
                         + (m.email IS NOT NULL)::INT + (m.address IS NOT NULL)::INT))::SMALLINT AS completeness,
                   m.conflicts, m.source_updated_at,
                   -- Which source supplied each attribute (field-level provenance).
                   json_object('name', p.source, 'location', p.source, 'category', p.source,
                               'website', coalesce(m.website_source, CASE WHEN m.website_any IS NOT NULL THEN p.source END),
                               'phone', m.phone_source, 'email', m.email_source, 'address', m.address_source)::VARCHAR
                       AS field_sources,
                   CASE WHEN list_contains(m.sources, 'osm') AND len(m.sources) = 1 THEN NULL ELSE m.confidence END
                       AS source_confidence,
                   m.wikidata_own AS wikidata_id, []::VARCHAR[] AS enriched_by
            FROM primary_member p JOIN merged m USING (gold_id)
            WHERE m.confidence >= {MIN_GOLD_CONFIDENCE}
        """)
        ctx.progress(0, None, "records", "linking organizations", force=True)
        org_stats = organizations.cluster_records(con, organizations.load_organization_links(country))
        organizations.assign_business_organizations(con)
        organizations.build_organizations(con, country)
        totals = con.execute("SELECT count(*), sum(member_count) FROM businesses").fetchone()
        excluded_low_confidence = con.execute(
            "SELECT count(DISTINCT gold_id) FROM members WHERE gold_id NOT IN (SELECT gold_id FROM businesses)"
        ).fetchone()[0]

        common = dict(run_id=ctx.run_id, versions=versions, inputs=inputs)
        partition = {"country": country}
        written = {}

        def write(dataset_id: str, sql: str):
            with DatasetVersionWriter(ctx.storage, dataset_id, partition, None, **common) as writer:
                copy_query_into(writer, con, sql)
            written[dataset_id] = writer.version

        build = f"(SELECT {ctx.run_id}::BIGINT AS pipeline_run_id, now()::TIMESTAMPTZ AS built_at)"
        write("gold.businesses", f"SELECT b.*, r.built_at FROM businesses b, {build} r")
        write("gold.business_sources", f"""
            SELECT m.gold_id, m.silver_id, '{country}' AS country_code, m.source, m.source_record_id, m.artifact_id,
                   v.silver_version_id, m.pipeline_run_id, m.transformation_version,
                   m.link_confidence AS match_confidence, m.member_rank = 1 AS is_primary
            FROM members m JOIN silver_versions v USING (source)
            WHERE m.gold_id IN (SELECT gold_id FROM businesses)
        """)
        write("gold.organizations", "SELECT * FROM organizations")
        write("gold.organization_sources", f"""
            SELECT m.organization_id, m.silver_id, '{country}' AS country_code, m.source, m.source_record_id,
                   m.link_method, m.source_confidence AS confidence, m.artifact_id, m.pipeline_run_id,
                   m.transformation_version, m.source_url
            FROM organization_members m WHERE m.organization_id IN (SELECT organization_id FROM organizations)
        """)
        write("gold.domains", domains_sql(country))
        con.execute(geo_places_view(geo_places))
        write("gold.cities", cities_sql(country))
    finally:
        con.close()

    ctx.put(gold_versions={d: v["id"] for d, v in written.items()})
    return StageResult(
        records_in=int(totals[1] or 0), records_out=int(totals[0]),
        metrics={"businesses": int(totals[0]), "source_records": int(totals[1] or 0),
                 "merged_away": int((totals[1] or 0) - totals[0]), "excluded_unnamed": int(excluded_unnamed),
                 "excluded_low_confidence": int(excluded_low_confidence),
                 "organizations": written["gold.organizations"]["row_count"],
                 "domains": written["gold.domains"]["row_count"], "cities": written["gold.cities"]["row_count"],
                 **org_stats, "er_fingerprint": fingerprint},
    )


def domains_sql(country: str) -> str:
    """Domains used by businesses, stated by organization records, or discovered by a crawl."""
    return f"""
        WITH b AS (
            SELECT domain, any_value(tld) AS tld, count(*)::INTEGER AS business_count,
                   count(DISTINCT organization_id)::INTEGER AS organization_count,
                   list_sort(list_distinct(flatten(list(sources)))) AS sources,
                   mode(category_group) AS top_category_group, mode(organization_id) AS organization_id
            FROM businesses WHERE domain IS NOT NULL GROUP BY domain
        ),
        o AS (
            SELECT normalized_domain AS domain, any_value(tld) AS tld, any_value(organization_id) AS organization_id,
                   list(DISTINCT source ORDER BY source) AS sources
            FROM organization_members
            WHERE normalized_domain IS NOT NULL AND NOT coalesce(domain_is_platform, false)
            GROUP BY normalized_domain
        ),
        w AS (SELECT * FROM web_domains)
        SELECT domain, '{country}' AS country_code, coalesce(b.tld, o.tld, w.tld) AS tld,
               coalesce(b.business_count, 0) AS business_count,
               greatest(coalesce(b.organization_count, 0), (o.organization_id IS NOT NULL)::INTEGER) AS organization_count,
               list_sort(list_distinct(coalesce(b.sources, []::VARCHAR[]) || coalesce(o.sources, []::VARCHAR[])
                                       || CASE WHEN w.domain IS NOT NULL THEN ['commoncrawl'] ELSE []::VARCHAR[] END))
                   AS sources,
               b.top_category_group, coalesce(b.organization_id, o.organization_id) AS organization_id,
               w.domain_type, w.is_business_candidate, w.pages AS crawl_pages, w.captures AS crawl_captures,
               w.host_count AS crawl_hosts, w.first_seen AS crawl_first_seen, w.last_seen AS crawl_last_seen,
               w.languages AS crawl_languages, w.homepage_url, w.homepage_status, w.crawl
        FROM b FULL OUTER JOIN o USING (domain) FULL OUTER JOIN w USING (domain)
        WHERE coalesce(b.tld, o.tld, w.tld) IS NOT NULL
    """


def geo_places_view(geo_places: list[dict]) -> str:
    """The preferred gazetteer's places, with gazetteer columns present even for sources that lack them."""
    files = dataset_files("silver.geo_places", versions=geo_places)
    if not files:
        columns = ", ".join(f"NULL::{'BIGINT' if f.name in ('population', 'artifact_id', 'pipeline_run_id') else 'DOUBLE' if f.name in ('latitude', 'longitude') else 'VARCHAR'} AS {f.name}"
                            for f in GEO_PLACES_SCHEMA if f.name in ("place_id", "source", "name", "kind", "latitude",
                                                                      "longitude", "population", "name_local",
                                                                      "admin1", "admin2"))
        return f"CREATE OR REPLACE TEMP VIEW geo_places AS SELECT {columns} WHERE false"
    return f"""
        CREATE OR REPLACE TEMP VIEW geo_places AS
        SELECT place_id, source, name, kind, latitude, longitude, population,
               {_optional_columns(files, ("name_local", "admin1", "admin2"))}
        FROM {parquet_source(files)}
    """


def _optional_columns(files: list[str], names: tuple[str, ...]) -> str:
    con = duck()
    try:
        present = {r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {parquet_source(files)}").fetchall()}
    finally:
        con.close()
    return ", ".join(name if name in present else f"NULL::VARCHAR AS {name}" for name in names)


def cities_sql(country: str) -> str:
    """Cities and towns with business coverage. Same-named places: counts go to the most populous one."""
    return f"""
        WITH counts AS (
            SELECT city AS name, count(*)::INTEGER AS business_count,
                   count(*) FILTER (WHERE has_website)::INTEGER AS with_website,
                   count(*) FILTER (WHERE has_phone)::INTEGER AS with_phone,
                   count(DISTINCT organization_id)::INTEGER AS organizations,
                   mode(category_group) AS top_category_group, mode(admin1) AS admin1
            FROM businesses WHERE city IS NOT NULL GROUP BY city
        ),
        ranked AS (
            SELECT *, row_number() OVER (PARTITION BY name ORDER BY coalesce(population, 0) DESC,
                                         CASE kind WHEN 'city' THEN 0 WHEN 'town' THEN 1 ELSE 2 END, place_id) AS rn
            FROM geo_places
        )
        SELECT r.place_id AS city_id, '{country}' AS country_code, r.name, r.name_local, r.kind,
               coalesce(c.admin1, r.admin1) AS admin1, r.admin2, r.latitude, r.longitude, r.population,
               coalesce(c.business_count, 0) AS business_count, coalesce(c.with_website, 0) AS with_website,
               coalesce(c.with_phone, 0) AS with_phone, coalesce(c.organizations, 0) AS organizations,
               CASE WHEN r.population > 0 AND c.business_count > 0
                    THEN round(10000.0 * c.business_count / r.population, 2) END AS businesses_per_10k,
               c.top_category_group, r.source
        FROM ranked r LEFT JOIN counts c ON c.name = r.name AND r.rn = 1
        WHERE r.kind IN ('city', 'town') OR (r.rn = 1 AND c.business_count > 0)
    """
