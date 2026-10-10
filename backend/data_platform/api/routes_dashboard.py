"""Analytics dashboards. Gold widgets take the same filter DSL as the Data Explorer (?filters=<json>),
so every number can be drilled into the explorer with identical conditions."""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import ValidationError

from .. import db
from ..errors import ParameterError
from ..lake.reader import dataset_files, duck, parquet_source
from ..query.dsl import POSTGRES, Group, compile_filters
from ..query.registry import get_dataset
from ..query.service import jsonable
from ..sources.registry import adapters, counted_artifacts_sql
from .auth import VIEW, Principal, require

router = APIRouter(prefix="/api/data-platform/dashboard", tags=["data-platform-dashboard"])
Viewer = Annotated[Principal, Depends(require(VIEW))]
GOLD = get_dataset("gold.businesses")
SILVER = get_dataset("silver.places")


def parse_filters(raw: str | None) -> Group | None:
    if not raw:
        return None
    try:
        return Group.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValidationError) as error:
        raise HTTPException(422, f"Invalid filters: {error}") from None


def gold_where(raw: str | None, alias: str | None = None) -> tuple[str, list]:
    try:
        sql, params = compile_filters(parse_filters(raw), GOLD.column_map, POSTGRES)
    except ParameterError as error:
        raise HTTPException(422, str(error)) from None
    return sql, params


def q(sql: str, params=None) -> list[dict]:
    with db.serving() as conn:
        return conn.execute(sql, params or []).fetchall()


def control(sql: str, params=None) -> list[dict]:
    with db.control() as conn:
        return conn.execute(sql, params or []).fetchall()


def layer_counts() -> dict:
    """Raw (current artifacts, see counted_artifacts_sql), Silver and Gold row counts from the catalog — no scans."""
    rows = control(f"""
        WITH latest_artifacts AS ({counted_artifacts_sql()})
        SELECT d.layer, d.id AS dataset_id, coalesce(sum(v.row_count), 0)::bigint AS rows,
               coalesce(sum(v.size_bytes), 0)::bigint AS bytes
        FROM dp.datasets d
        JOIN dp.dataset_versions v ON v.dataset_id = d.id AND v.is_current
        WHERE d.layer <> 'bronze' OR (v.partition->>'artifact_id')::bigint IN (SELECT id FROM latest_artifacts)
        GROUP BY 1, 2
    """)
    return {r["dataset_id"]: r for r in rows}


@router.get("/overview")
def overview(_: Viewer, filters: str | None = None):
    where, params = gold_where(filters)
    kpi = q(f"""
        SELECT count(*) AS businesses,
               count(DISTINCT organization_id) AS organizations,
               count(DISTINCT (round(latitude::numeric, 4), round(longitude::numeric, 4))) AS locations,
               count(DISTINCT domain) AS domains,
               count(*) FILTER (WHERE has_website) AS with_website,
               count(*) FILTER (WHERE has_phone) AS with_phone,
               count(*) FILTER (WHERE has_address) AS with_address,
               count(*) FILTER (WHERE has_email) AS with_email,
               count(DISTINCT country_code) AS countries,
               count(DISTINCT city) AS cities,
               round(avg(completeness), 1) AS avg_completeness,
               max(source_updated_at) AS latest_source_update
        FROM gold.businesses WHERE {where}
    """, params)[0]
    counts = layer_counts()
    raw = sum(r["rows"] for d, r in counts.items() if d.startswith("bronze."))
    silver = sum(r["rows"] for d, r in counts.items() if d.startswith("silver.") and d != "silver.rejects")
    pipeline = control("""
        SELECT count(*) FILTER (WHERE status IN ('COMPLETED', 'PARTIALLY_COMPLETED')) AS succeeded,
               count(*) FILTER (WHERE status = 'FAILED') AS failed,
               count(*) FILTER (WHERE status IN ('COMPLETED', 'PARTIALLY_COMPLETED', 'FAILED', 'CANCELLED')) AS finished
        FROM dp.pipeline_runs WHERE created_at > now() - interval '30 days'
    """)[0]
    sources = control("""
        SELECT count(DISTINCT s.id) AS active FROM dp.data_sources s
        JOIN dp.pipeline_runs r ON r.source_id = s.id AND r.status IN ('COMPLETED', 'PARTIALLY_COMPLETED')
        WHERE s.enabled
    """)[0]
    storage = control("""
        SELECT (SELECT coalesce(sum(size_bytes), 0) FROM dp.dataset_artifacts WHERE status = 'available')
             + (SELECT coalesce(sum(size_bytes), 0) FROM dp.dataset_versions WHERE status = 'committed') AS bytes,
               (SELECT max(source_release_date) FROM dp.dataset_artifacts WHERE status = 'available') AS latest_release
    """)[0]
    by_source = q(f"""
        SELECT s AS source, count(*) AS businesses FROM gold.businesses, unnest(sources) s
        WHERE {where} GROUP BY 1 ORDER BY 2 DESC
    """, params)
    return jsonable({
        "contributions": source_contributions(where, params, by_source),
        "kpis": {**kpi, "raw_records": raw, "silver_records": silver,
                 "sources_active": sources["active"],
                 "pipeline_success_rate": (round(100 * pipeline["succeeded"] / pipeline["finished"], 1)
                                           if pipeline["finished"] else None),
                 "pipeline_runs_30d": pipeline["finished"], "storage_bytes": storage["bytes"],
                 "latest_release": storage["latest_release"]},
        "layers": counts,
        "by_source": by_source,
    })


def source_contributions(where: str, params: list, by_source: list[dict]) -> list[dict]:
    """What every registered source adds: its own Silver records, and its part in Gold.

    Location sources *find* businesses; reference sources *enrich* them (through their organization, website or
    news) and add organizations and domains of their own. Gold figures respect the dashboard filters.
    """
    silver = control("""
        SELECT v.partition->>'source' AS source, v.dataset_id, sum(v.row_count)::bigint AS rows
        FROM dp.dataset_versions v
        WHERE v.is_current AND v.dataset_id LIKE 'silver.%%' AND v.dataset_id <> 'silver.rejects'
        GROUP BY 1, 2
    """)
    status = {r["source_id"]: r for r in control("""
        SELECT DISTINCT ON (source_id) source_id, status, finished_at FROM dp.pipeline_runs
        ORDER BY source_id, created_at DESC
    """)}
    enriched = {r["source"]: r["businesses"] for r in q(f"""
        SELECT e AS source, count(*) AS businesses FROM gold.businesses, unnest(enriched_by) e
        WHERE {where} GROUP BY 1
    """, params)}
    organizations = {r["source"]: r["n"] for r in q(
        "SELECT s AS source, count(*) AS n FROM gold.organizations, unnest(sources) s GROUP BY 1")}
    domains = {r["source"]: r["n"] for r in q(
        "SELECT s AS source, count(*) AS n FROM gold.domains, unnest(sources) s GROUP BY 1")}
    found = {r["source"]: r["businesses"] for r in by_source}
    from ..config import get_settings

    result = []
    for adapter in adapters().values():
        sid = adapter.source_id
        last = status.get(sid) or {}
        result.append({
            "source": sid, "name": adapter.name, "role": adapter.role,
            "status": last.get("status"), "last_finished": last.get("finished_at"),
            "missing_credentials": [f"DP_SECRET_{c}" for c in adapter.capabilities.credentials
                                    if not get_settings().secret(c)],
            "silver": {r["dataset_id"]: r["rows"] for r in silver if r["source"] == sid},
            "businesses_found": found.get(sid, 0), "businesses_enriched": enriched.get(sid, 0),
            "organizations": organizations.get(sid, 0), "domains": domains.get(sid, 0),
        })
    return result


@router.get("/sources")
def sources(_: Viewer, filters: str | None = None):
    where, params = gold_where(filters)
    contribution = q(f"""
        WITH b AS (SELECT * FROM gold.businesses WHERE {where})
        SELECT s AS source,
               count(*) AS gold_contributions,
               count(*) FILTER (WHERE b.source_count = 1) AS unique_contributions,
               count(*) FILTER (WHERE b.source_count > 1) AS overlapping,
               round(100.0 * avg(b.has_website::int), 1) AS website_pct,
               round(100.0 * avg(b.has_phone::int), 1) AS phone_pct,
               round(100.0 * avg(b.has_address::int), 1) AS address_pct,
               count(DISTINCT b.city) AS cities, count(DISTINCT b.country_code) AS countries
        FROM b, unnest(b.sources) s GROUP BY s ORDER BY 2 DESC
    """, params)
    counts = layer_counts()
    silver_by_source = control("""
        SELECT v.partition->>'source' AS source, sum(v.row_count)::bigint AS rows
        FROM dp.dataset_versions v WHERE v.dataset_id = 'silver.places' AND v.is_current GROUP BY 1
    """)
    bronze_by_source = control(f"""
        WITH latest AS ({counted_artifacts_sql()})
        SELECT l.source_id AS source, sum(v.row_count)::bigint AS rows FROM dp.dataset_versions v
        JOIN latest l ON (v.partition->>'artifact_id')::bigint = l.id
        WHERE v.is_current GROUP BY 1
    """)
    multi = q(f"""
        SELECT source_count, count(*) AS businesses FROM gold.businesses WHERE {where}
        GROUP BY 1 ORDER BY 1
    """, params)
    silver = {r["source"]: r["rows"] for r in silver_by_source}
    bronze = {r["source"]: r["rows"] for r in bronze_by_source}
    rows = []
    for row in contribution:
        rows.append({**row, "bronze_records": bronze.get(row["source"]), "silver_records": silver.get(row["source"]),
                     "overlap_rate": round(100 * row["overlapping"] / row["gold_contributions"], 1)
                     if row["gold_contributions"] else None})
    return jsonable({"sources": rows, "by_source_count": multi, "layers": counts})


@router.get("/overlap")
def overlap(_: Viewer, filters: str | None = None):
    """Which sources found each business, and which fields one source added to another's record."""
    where, params = gold_where(filters)
    combinations = q(f"""
        SELECT sources, count(*) AS businesses,
               round(100.0 * avg(has_website::int), 1) AS website_pct,
               round(100.0 * avg(has_phone::int), 1) AS phone_pct,
               round(100.0 * avg(has_email::int), 1) AS email_pct
        FROM gold.businesses WHERE {where} GROUP BY sources ORDER BY 2 DESC
    """, params)
    enrichment = q(f"""
        SELECT f AS field, field_sources->>f AS by, field_sources->>'name' AS primary_source, count(*) AS businesses
        FROM gold.businesses, unnest(ARRAY['website', 'phone', 'email', 'address']) AS f
        WHERE {where} AND source_count > 1 AND field_sources->>f IS NOT NULL
          AND field_sources->>f <> field_sources->>'name'
        GROUP BY 1, 2, 3 ORDER BY 4 DESC
    """, params)
    per_source = q(f"""
        SELECT s AS source, count(*) AS businesses,
               count(*) FILTER (WHERE source_count = 1) AS only_this_source,
               count(*) FILTER (WHERE source_count > 1) AS shared
        FROM gold.businesses, unnest(sources) s WHERE {where} GROUP BY 1 ORDER BY 2 DESC
    """, params)
    # Sources that describe a business without being a location record: Wikidata / registries via its
    # organization, news mentions, and the crawl of its website.
    enriched_by = q(f"""
        SELECT s AS source, e AS by, count(*) AS businesses
        FROM gold.businesses, unnest(sources) s, unnest(enriched_by) e
        WHERE {where} GROUP BY 1, 2 ORDER BY 3 DESC
    """, params)
    enriched_totals = q(f"""
        SELECT e AS by, count(*) AS businesses FROM gold.businesses, unnest(enriched_by) e
        WHERE {where} GROUP BY 1 ORDER BY 2 DESC
    """, params)
    return jsonable({"combinations": combinations, "enrichment": enrichment, "per_source": per_source,
                     "enriched_by": enriched_by, "enriched_totals": enriched_totals})


@router.get("/geography")
def geography(_: Viewer, filters: str | None = None, zoom: Annotated[int, Query(ge=1, le=18)] = 5,
              west: float | None = None, south: float | None = None, east: float | None = None,
              north: float | None = None):
    where, params = gold_where(filters)
    by_country = q(f"SELECT country_code AS label, count(*) AS count FROM gold.businesses WHERE {where} "
                   "GROUP BY 1 ORDER BY 2 DESC", params)
    by_region = q(f"SELECT admin1 AS label, count(*) AS count FROM gold.businesses WHERE {where} AND admin1 IS NOT NULL "
                  "GROUP BY 1 ORDER BY 2 DESC LIMIT 40", params)
    by_city = q(f"SELECT city AS label, admin1 AS parent, count(*) AS count FROM gold.businesses WHERE {where} "
                "AND city IS NOT NULL GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 40", params)
    # Server-side density grid: cell size halves with every zoom level; never ships raw points.
    cell = 360.0 / (2 ** (zoom + 3))
    bbox = ""
    bbox_params: list = []
    if None not in (west, south, east, north):
        bbox = "AND longitude BETWEEN %s AND %s AND latitude BETWEEN %s AND %s"
        bbox_params = [west, east, south, north]
    grid = q(f"""
        SELECT floor(latitude / %s) AS gy, floor(longitude / %s) AS gx, count(*) AS count,
               avg(latitude) AS latitude, avg(longitude) AS longitude
        FROM gold.businesses WHERE {where} {bbox} AND latitude IS NOT NULL
        GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 5000
    """, [cell, cell] + params + bbox_params)
    return jsonable({"by_country": by_country, "by_region": by_region, "by_city": by_city,
                     "grid": {"cell_degrees": cell, "cells": grid}})


@router.get("/cities")
def cities(_: Viewer, filters: str | None = None):
    """Business coverage per city against population (gazetteer), to spot under-mapped cities."""
    countries = gold_countries(filters)
    scope, params = ("country_code = ANY(%s)", [countries]) if countries else ("true", [])
    summary = q(f"""
        SELECT count(*) FILTER (WHERE kind = 'city') AS cities, count(*) FILTER (WHERE kind = 'town') AS towns,
               count(*) FILTER (WHERE population IS NOT NULL) AS with_population,
               count(*) FILTER (WHERE business_count > 0) AS with_businesses, max(source) AS gazetteer
        FROM gold.cities WHERE {scope}
    """, params)[0]
    columns = "city_id, name, name_local, kind, admin1, population, business_count, businesses_per_10k, with_website"
    largest = q(f"""SELECT {columns} FROM gold.cities WHERE {scope} AND population IS NOT NULL
                    ORDER BY population DESC LIMIT 25""", params)
    # Large places with few mapped businesses per resident: where more sources / mapping would help most.
    underserved = q(f"""SELECT {columns} FROM gold.cities WHERE {scope} AND population >= 100000
                        ORDER BY coalesce(businesses_per_10k, 0), population DESC LIMIT 15""", params)
    return jsonable({"summary": summary, "largest": largest, "underserved": underserved})


@router.get("/categories")
def categories(_: Viewer, filters: str | None = None, group: str | None = None):
    where, params = gold_where(filters)
    groups = q(f"SELECT category_group AS label, count(*) AS count FROM gold.businesses WHERE {where} "
               "GROUP BY 1 ORDER BY 2 DESC", params)
    category_where = where + (" AND category_group = %s" if group else "")
    category_params = params + ([group] if group else [])
    top = q(f"SELECT category AS label, category_group AS parent, count(*) AS count FROM gold.businesses "
            f"WHERE {category_where} GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 30", category_params)
    by_source = q(f"""
        SELECT s AS source, category_group AS label, count(*) AS count
        FROM gold.businesses, unnest(sources) s WHERE {where} GROUP BY 1, 2 ORDER BY 3 DESC
    """, params)
    city_matrix = q(f"""
        WITH top_cities AS (SELECT city FROM gold.businesses WHERE {where} AND city IS NOT NULL
                            GROUP BY city ORDER BY count(*) DESC LIMIT 12)
        SELECT city, category_group AS label, count(*) AS count FROM gold.businesses
        WHERE {where} AND city IN (SELECT city FROM top_cities) GROUP BY 1, 2
    """, params + params)
    return jsonable({"groups": groups, "categories": top, "by_source": by_source, "by_city": city_matrix})


@router.get("/domains")
def domains(_: Viewer, filters: str | None = None):
    where, params = gold_where(filters)
    summary = q(f"""
        SELECT count(DISTINCT domain) AS unique_domains,
               count(*) FILTER (WHERE domain IS NOT NULL) AS with_domain,
               count(*) FILTER (WHERE domain IS NULL) AS without_domain,
               count(*) FILTER (WHERE domain IS NULL AND website IS NOT NULL) AS platform_only,
               count(*) AS businesses
        FROM gold.businesses WHERE {where}
    """, params)[0]
    tlds = q(f"SELECT tld AS label, count(DISTINCT domain) AS domains, count(*) AS businesses FROM gold.businesses "
             f"WHERE {where} AND tld IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 25", params)
    coverage = {}
    for key, expression in [("by_category", "category_group"), ("by_region", "admin1")]:
        coverage[key] = q(f"""
            SELECT {expression} AS label, count(*) AS businesses,
                   round(100.0 * avg((domain IS NOT NULL)::int), 1) AS domain_pct
            FROM gold.businesses WHERE {where} AND {expression} IS NOT NULL
            GROUP BY 1 ORDER BY 2 DESC LIMIT 20
        """, params)
    coverage["by_source"] = q(f"""
        SELECT s AS label, count(*) AS businesses, round(100.0 * avg((domain IS NOT NULL)::int), 1) AS domain_pct
        FROM gold.businesses, unnest(sources) s WHERE {where} GROUP BY 1 ORDER BY 2 DESC
    """, params)
    top = q(f"""
        SELECT domain AS label, count(*) AS businesses, count(DISTINCT city) AS cities
        FROM gold.businesses WHERE {where} AND domain IS NOT NULL GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 25
    """, params)
    crawl = crawl_summary(filters)
    return jsonable({"summary": summary, "tlds": tlds, "top_domains": top, **coverage, "crawl": crawl})


def gold_countries(filters: str | None) -> list[str] | None:
    """Country scope implied by the dashboard filters (organizations/domains/cities are per country)."""
    group = parse_filters(filters)
    for condition in (group.conditions if group else []):
        if getattr(condition, "field", None) == "country_code" and condition.value:
            return list(condition.value) if isinstance(condition.value, list) else [condition.value]
    return None


def crawl_summary(filters: str | None) -> dict:
    """Web-crawl view of domains: how many business domains the crawl knows, and what it discovered."""
    countries = gold_countries(filters)
    scope, params = ("country_code = ANY(%s)", [countries]) if countries else ("true", [])
    summary = q(f"""
        SELECT count(*) FILTER (WHERE business_count > 0) AS business_domains,
               count(*) FILTER (WHERE business_count > 0 AND crawl_pages IS NOT NULL) AS business_domains_crawled,
               count(*) FILTER (WHERE crawl_pages IS NOT NULL) AS crawled_domains,
               count(*) FILTER (WHERE business_count = 0 AND organization_count = 0 AND crawl_pages IS NOT NULL)
                   AS discovered_only,
               count(*) FILTER (WHERE business_count = 0 AND organization_count = 0 AND is_business_candidate)
                   AS discovered_candidates,
               count(*) FILTER (WHERE homepage_status BETWEEN 200 AND 299) AS live_homepages,
               max(crawl) AS crawl
        FROM gold.domains WHERE {scope}
    """, params)[0]
    by_type = q(f"""
        SELECT coalesce(domain_type, 'not crawled') AS label, count(*) AS domains,
               count(*) FILTER (WHERE business_count > 0) AS with_businesses
        FROM gold.domains WHERE {scope} GROUP BY 1 ORDER BY 2 DESC
    """, params)
    languages = q(f"""
        SELECT l AS label, count(*) AS domains FROM gold.domains, unnest(crawl_languages[1:1]) l
        WHERE {scope} GROUP BY 1 ORDER BY 2 DESC LIMIT 10
    """, params)
    leads = q(f"""
        SELECT domain AS label, domain_type, crawl_pages, homepage_status, crawl_languages[1] AS language
        FROM gold.domains
        WHERE {scope} AND business_count = 0 AND organization_count = 0 AND is_business_candidate
        ORDER BY crawl_pages DESC NULLS LAST, domain LIMIT 25
    """, params)
    return {"summary": summary, "by_type": by_type, "languages": languages, "discovery_leads": leads}


@router.get("/organizations")
def organizations(_: Viewer, filters: str | None = None):
    """Organizations: what locations imply, what Wikidata / registries state, and how often the news names them."""
    from ..normalize import name_key

    countries = gold_countries(filters)
    scope, params = ("country_code = ANY(%s)", [countries]) if countries else ("true", [])
    kpis = q(f"""
        SELECT count(*) AS organizations,
               count(*) FILTER (WHERE location_count > 0) AS with_locations,
               count(*) FILTER (WHERE location_count >= 2) AS multi_location,
               count(*) FILTER (WHERE location_count = 0) AS records_only,
               count(*) FILTER (WHERE wikidata_id IS NOT NULL) AS with_wikidata,
               count(*) FILTER (WHERE wikidata_id IS NOT NULL AND location_count > 0) AS located_with_wikidata,
               count(*) FILTER (WHERE registry_id IS NOT NULL) AS with_registry,
               count(*) FILTER (WHERE news_mentions > 0) AS in_news,
               coalesce(sum(news_mentions), 0) AS news_mentions
        FROM gold.organizations WHERE {scope}
    """, params)[0]
    by_source = q(f"""
        SELECT s AS label, count(*) AS organizations, count(*) FILTER (WHERE location_count > 0) AS with_locations
        FROM gold.organizations, unnest(sources) s WHERE {scope} GROUP BY 1 ORDER BY 2 DESC
    """, params)
    by_category = q(f"""
        SELECT coalesce(category_group, 'Unknown') AS label, count(*) AS organizations,
               count(*) FILTER (WHERE location_count > 0) AS with_locations
        FROM gold.organizations WHERE {scope} GROUP BY 1 ORDER BY 2 DESC
    """, params)
    columns = "organization_id, name, location_count, city_count, sources, wikidata_id, registry_id, news_mentions, industry"
    top_located = q(f"SELECT {columns} FROM gold.organizations WHERE {scope} ORDER BY location_count DESC, name LIMIT 20",
                    params)
    top_news = q(f"""SELECT {columns}, news_tone FROM gold.organizations WHERE {scope} AND news_mentions > 0
                     ORDER BY news_mentions DESC, name LIMIT 20""", params)
    links = q("""
        SELECT evidence->>'method' AS method, status, count(*) AS links FROM er.entity_matches
        WHERE match_type = 'organization' GROUP BY 1, 2 ORDER BY 3 DESC
    """)
    # News mentions that match no organization: discovery leads for registries / knowledge bases.
    known = {name_key(r["name"]) for r in q(f"SELECT name FROM gold.organizations WHERE {scope}", params)}
    known |= {name_key(a) for r in q(f"SELECT unnest(aliases) AS a FROM gold.organizations WHERE {scope}", params)
              for a in [r["a"]]}
    unmatched = []
    files = dataset_files("silver.org_mentions", {"source": "gdelt"})
    if files:
        con = duck()
        try:
            for row in con.execute(f"""
                SELECT name_key, name, mentions, articles, last_seen FROM {parquet_source(files)}
                {"WHERE country_code IN (" + ", ".join("?" * len(countries)) + ")" if countries else ""}
                ORDER BY mentions DESC LIMIT 400
            """, countries or []).fetchall():
                if row[0] not in known and len(unmatched) < 20:
                    unmatched.append(dict(zip(["name_key", "name", "mentions", "articles", "last_seen"], row)))
        finally:
            con.close()
    return jsonable({"kpis": kpis, "by_source": by_source, "by_category": by_category, "top_located": top_located,
                     "top_news": top_news, "links": links, "unmatched_news": unmatched})


SILVER_FILTER_FIELDS = {"country_code", "admin1", "city", "category_group", "category", "source", "entity_type"}


@router.get("/quality")
def quality(_: Viewer, filters: str | None = None):
    """Coverage and validity per source, from Silver (every observation) plus ER and Gold conflicts."""
    group = parse_filters(filters)
    ignored = []
    if group:
        kept = []
        for cond in group.conditions:
            if getattr(cond, "field", None) in SILVER_FILTER_FIELDS:
                kept.append(cond)
            else:
                ignored.append(getattr(cond, "field", "group"))
        group = Group(operator=group.operator, conditions=kept)
    files = dataset_files("silver.places")
    per_source: list[dict] = []
    if files:
        try:
            where, params = compile_filters(group, SILVER.column_map, SILVER.dialect())
        except ParameterError as error:
            raise HTTPException(422, str(error)) from None
        con = duck()
        try:
            result = con.execute(f"""
                SELECT source, count(*) AS records,
                       count(*) FILTER (WHERE NOT has_name) AS missing_names,
                       count(*) FILTER (WHERE category IS NULL) AS missing_categories,
                       count(*) FILTER (WHERE address IS NULL) AS missing_addresses,
                       count(*) FILTER (WHERE website IS NULL) AS missing_websites,
                       count(*) FILTER (WHERE country_code IS NULL) AS unassigned_country,
                       count(*) FILTER (WHERE json_extract_string(attributes, '$.website') IS NOT NULL
                                        AND normalized_domain IS NULL) AS invalid_domains,
                       count(*) FILTER (WHERE phone IS NOT NULL AND normalized_phone IS NULL) AS invalid_phones,
                       round(100.0 * avg((normalized_domain IS NOT NULL AND NOT domain_is_platform)::int), 1) AS website_pct,
                       round(100.0 * avg((normalized_phone IS NOT NULL)::int), 1) AS phone_pct,
                       round(100.0 * avg((address IS NOT NULL)::int), 1) AS address_pct,
                       round(100.0 * avg(has_name::int), 1) AS name_pct
                FROM {parquet_source(files)} WHERE {where} GROUP BY source ORDER BY records DESC
            """, params)
            names = [d[0] for d in result.description]
            per_source = [dict(zip(names, row)) for row in result.fetchall()]
        finally:
            con.close()

    rejects = []
    reject_files = dataset_files("silver.rejects")
    if reject_files:
        con = duck()
        try:
            rejects = [{"source": s, "reason": r, "count": n} for s, r, n in con.execute(
                f"SELECT source, reason, count(*) FROM {parquet_source(reject_files)} GROUP BY 1, 2 ORDER BY 3 DESC"
            ).fetchall()]
        finally:
            con.close()

    where, params = gold_where(filters)
    matches = q("SELECT status, count(*) AS count FROM er.entity_matches GROUP BY 1")
    conflicts = q(f"""
        SELECT c AS conflict, count(*) AS count FROM gold.businesses, unnest(conflicts) c
        WHERE {where} GROUP BY 1 ORDER BY 2 DESC
    """, params)
    checks = control("""
        SELECT DISTINCT ON (dataset_id, check_name) dataset_id, check_name, check_type, severity, status,
               records_checked, records_failed, failure_percentage, threshold_percentage, created_at, pipeline_run_id
        FROM dp.quality_results ORDER BY dataset_id, check_name, created_at DESC
    """)
    gold_scores = q(f"SELECT round(avg(completeness), 1) AS avg_completeness, count(*) AS businesses, "
                    f"count(*) FILTER (WHERE cardinality(conflicts) > 0) AS conflicted "
                    f"FROM gold.businesses WHERE {where}", params)[0]
    passed = sum(1 for c in checks if c["status"] == "passed")
    return jsonable({
        "per_source": per_source, "rejects": rejects, "matches": matches, "conflicts": conflicts,
        "checks": checks, "check_pass_rate": round(100 * passed / len(checks), 1) if checks else None,
        "gold": gold_scores, "ignored_filters": ignored,
    })


@router.get("/pipelines")
def pipelines(_: Viewer, days: Annotated[int, Query(ge=1, le=365)] = 30):
    summary = control("""
        SELECT count(*) AS runs,
               count(*) FILTER (WHERE status IN ('COMPLETED', 'PARTIALLY_COMPLETED')) AS succeeded,
               count(*) FILTER (WHERE status = 'FAILED') AS failed,
               count(*) FILTER (WHERE status = 'CANCELLED') AS cancelled,
               count(*) FILTER (WHERE status NOT IN ('COMPLETED', 'PARTIALLY_COMPLETED', 'FAILED', 'CANCELLED')) AS active,
               round(avg(extract(epoch FROM finished_at - started_at)) FILTER (WHERE status = 'COMPLETED')::numeric, 1)
                   AS avg_duration_seconds,
               coalesce(sum(bytes_downloaded), 0)::bigint AS bytes_downloaded,
               coalesce(sum(bronze_records + silver_records + gold_records), 0)::bigint AS records
        FROM dp.pipeline_runs WHERE created_at > now() - make_interval(days => %s)
    """, [days])[0]
    stage_stats = control("""
        SELECT s.stage, count(*) FILTER (WHERE s.status = 'SUCCEEDED') AS executed,
               count(*) FILTER (WHERE s.status = 'SKIPPED') AS skipped,
               count(*) FILTER (WHERE s.status = 'FAILED') AS failed,
               coalesce(sum(greatest(s.attempt - 1, 0)), 0)::int AS retries,
               round(avg(extract(epoch FROM s.finished_at - s.started_at)) FILTER (WHERE s.status = 'SUCCEEDED')::numeric, 1)
                   AS avg_seconds,
               round(avg((s.metrics->>'records_per_sec')::float) FILTER (WHERE s.status = 'SUCCEEDED')::numeric)
                   AS avg_records_per_sec
        FROM dp.pipeline_stage_runs s JOIN dp.pipeline_runs r ON r.id = s.run_id
        WHERE r.created_at > now() - make_interval(days => %s)
        GROUP BY s.stage, s.position ORDER BY s.position
    """, [days])
    over_time = control("""
        SELECT date_trunc('day', created_at)::date AS day, status, count(*) AS runs,
               coalesce(sum(bronze_records), 0)::bigint AS bronze, coalesce(sum(silver_records), 0)::bigint AS silver,
               coalesce(sum(gold_records), 0)::bigint AS gold
        FROM dp.pipeline_runs WHERE created_at > now() - make_interval(days => %s)
        GROUP BY 1, 2 ORDER BY 1
    """, [days])
    throughput = control("""
        SELECT r.id AS run_id, r.created_at, s.stage, (s.metrics->>'records_per_sec')::float AS records_per_sec
        FROM dp.pipeline_stage_runs s JOIN dp.pipeline_runs r ON r.id = s.run_id
        WHERE s.status = 'SUCCEEDED' AND s.metrics ? 'records_per_sec'
          AND s.stage IN ('BRONZE_INGEST', 'SILVER_TRANSFORM', 'GOLD_BUILD')
          AND r.created_at > now() - make_interval(days => %s)
        ORDER BY r.created_at
    """, [days])
    storage = control("""
        SELECT day, sum(bytes) OVER (ORDER BY day) AS cumulative_bytes FROM (
            SELECT date_trunc('day', created_at)::date AS day, sum(size_bytes)::bigint AS bytes FROM (
                SELECT created_at, size_bytes FROM dp.dataset_artifacts WHERE status = 'available'
                UNION ALL SELECT created_at, size_bytes FROM dp.dataset_versions WHERE status = 'committed'
            ) s GROUP BY 1
        ) d ORDER BY day
    """)
    freshness = control("""
        SELECT p.id AS pipeline, max(r.finished_at) FILTER (WHERE r.status IN ('COMPLETED', 'PARTIALLY_COMPLETED'))
                   AS last_success,
               (SELECT max(source_release_date) FROM dp.dataset_artifacts a WHERE a.source_id = p.source_id
                AND a.status = 'available') AS latest_data
        FROM dp.pipeline_definitions p LEFT JOIN dp.pipeline_runs r ON r.pipeline_definition_id = p.id
        GROUP BY p.id
    """)
    finished = summary["succeeded"] + summary["failed"] + summary["cancelled"]
    return jsonable({**summary, "failure_rate": round(100 * summary["failed"] / finished, 1) if finished else None,
                     "stages": stage_stats, "over_time": over_time, "throughput": throughput,
                     "storage_growth": storage, "freshness": freshness})
