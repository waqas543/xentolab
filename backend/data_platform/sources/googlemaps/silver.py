"""Google Maps Bronze -> Silver places: one row per Google place, merged across every stored observation.

The same place is observed many times: found by several searches, or scraped again later. All observations of a
place (same place id, else CID, else feature id) fold into one row; each field takes its newest non-empty value
(order: artifact, then line). The shared normalizers, taxonomy and geography are then applied as for every other
places source. Permanently closed places, records without an id and unusable coordinates go to silver.rejects;
places that are not businesses (mosques, parks, police) are dropped by the taxonomy.

Searches by town return businesses from other countries too (a "travel agency" search finds Dubai and Mumbai
agencies); a place outside every boundary of the searched country is rejected as `outside_country`. Points
where many listings stack up are Google's town defaults: they are flagged approximate and take the town the
search was made for.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

import pyarrow as pa

from ...engine.context import StageContext
from ...lake.reader import dataset_files, duck, parquet_source
from ...lake.writer import DatasetVersionWriter
from ...normalize import (
    name_key,
    normalize_name,
    normalize_phone,
    normalize_website,
    parse_google_hours,
    valid_coordinates,
)
from ...silver_contract import (
    PLACES_SCHEMA,
    REJECTS_SCHEMA,
    empty_columns,
    load_geo_index,
    location_precision,
    place_geography,
    stacked_points_sql,
)
from ...taxonomy import classify_googlemaps

SOURCE = "googlemaps"
BATCH_ROWS = 20_000
# Fields that take the newest non-empty observation.
LATEST = ("place_id", "cid", "feature_id", "name", "category", "address", "phone", "website", "domain", "hours",
          "rating", "reviews", "maps_url", "search_category", "search_category_group", "search_city", "search_area",
          "search_province")
# Scraper glitches put an address or plus code in the category ("G93M+9JV, Ahmed Baksh Rd", "Bahawalpur Rd").
_ADDRESS_LIKE = re.compile(r"[0-9a-z]{2,}\+|,|(?<![-\d])\d{2,}|\b(rd|road|street|chowk|colony|block|sector)\b|\bst\.?$",
                           re.IGNORECASE)


def merge_sql(source: str) -> str:
    latest = ",\n".join(f"arg_max({f}, seq) FILTER (WHERE {f} IS NOT NULL) AS {f}" for f in LATEST)
    return f"""
        WITH obs AS (
            SELECT *, coalesce(place_id, 'cid:' || cid, 'fid:' || feature_id) AS place_key,
                   artifact_id * 100000000 + line_number AS seq
            FROM {source}
        )
        SELECT place_key,
               {latest},
               arg_max(latitude, seq) FILTER (WHERE latitude IS NOT NULL AND longitude IS NOT NULL) AS latitude,
               arg_max(longitude, seq) FILTER (WHERE latitude IS NOT NULL AND longitude IS NOT NULL) AS longitude,
               bool_and(coalesce(sponsored, false)) AS sponsored_only,
               list(DISTINCT search_query ORDER BY search_query) FILTER (WHERE search_query IS NOT NULL)
                   AS search_queries,
               count(*) AS observations, count(DISTINCT record_hash) AS versions,
               min(ingested_at) AS first_seen_at, max(ingested_at) AS last_seen_at,
               arg_max(artifact_id, seq) AS artifact_id
        FROM obs
        WHERE place_key IS NOT NULL
        GROUP BY place_key
    """


def unidentified_sql(source: str) -> str:
    return f"""
        SELECT name, latitude, longitude, record, artifact_id FROM {source}
        WHERE place_id IS NULL AND cid IS NULL AND feature_id IS NULL
    """


def clean_category(category: str | None, city: str | None, area: str | None) -> str | None:
    category = (category or "").strip()
    if not category or _ADDRESS_LIKE.search(category) \
            or category.lower() in {(city or "").strip().lower(), (area or "").strip().lower()}:
        return None
    return category


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _reject(rejects: dict, reason: str, record_id, name, lat, lon, attributes: str, artifact_id, context: dict):
    for key, value in [("source", SOURCE), ("source_record_id", record_id), ("reason", reason), ("name", name),
                       ("latitude", lat), ("longitude", lon), ("attributes", attributes),
                       ("artifact_id", artifact_id), ("pipeline_run_id", context["run_id"]),
                       ("rejected_at", context["now"])]:
        rejects[key].append(value)


def normalize_batch(rows: list[dict], *, geo, scope_country: str, context: dict):
    places = empty_columns(PLACES_SCHEMA)
    rejects = empty_columns(REJECTS_SCHEMA)
    stats = {"excluded_non_business": 0, "outside_country": 0}

    kept = []
    for row in rows:
        category = clean_category(row["category"], row["search_city"], row["search_area"])
        attributes = json.dumps({
            "place_id": row["place_id"], "cid": row["cid"], "feature_id": row["feature_id"],
            "maps_url": row["maps_url"], "google_category": row["category"], "rating": row["rating"],
            "reviews": row["reviews"], "hours": row["hours"], "sponsored_only": row["sponsored_only"],
            "search_queries": (row["search_queries"] or [])[:20], "observations": row["observations"],
            "versions": row["versions"], "first_seen_at": _iso(row["first_seen_at"]),
            "last_seen_at": _iso(row["last_seen_at"]),
        }, ensure_ascii=False)
        reason = None
        if (row["hours"] or "").strip().lower() == "permanently closed":
            reason = "permanently_closed"
        elif not valid_coordinates(row["latitude"], row["longitude"]):
            reason = "invalid_coordinates"
        if reason:
            _reject(rejects, reason, row["place_key"], row["name"], row["latitude"], row["longitude"], attributes,
                    row["artifact_id"], context)
            continue
        classification = classify_googlemaps(category, row["search_category"], row["search_category_group"])
        if classification is None:
            stats["excluded_non_business"] += 1
            continue
        kept.append((row, classification, attributes))

    geo_values = geo.assign([r[0]["latitude"] for r in kept], [r[0]["longitude"] for r in kept]) if geo else None

    for index, (row, (category_key, category, group, entity_type), attributes) in enumerate(kept):
        country_code = geo_values["country_code"][index] if geo_values else scope_country
        if country_code is None or country_code.upper() != scope_country:
            # Outside every boundary of the searched country (or inside another one): a foreign search result.
            stats["outside_country"] += 1
            _reject(rejects, "outside_country", row["place_key"], row["name"], row["latitude"], row["longitude"],
                    attributes, row["artifact_id"], context)
            continue
        name = (row["name"] or "").strip() or None
        url, domain, tld, is_platform = normalize_website(row["website"] or row["domain"])
        precision = location_precision(row.get("stack_size"),
                                       geo_values["locality_distance_km"][index] if geo_values else None)
        geography = place_geography(geo, geo_values, index, precision=precision, stated_city=row["search_city"],
                                    stated_admin1=row["search_province"])
        values = {
            "silver_id": f"{SOURCE}:{row['place_key']}",
            "source": SOURCE,
            "source_record_id": row["place_key"],
            "source_entity_type": "place",
            "name": name,
            "name_local": None,
            "normalized_name": normalize_name(name),
            "name_key": name_key(name),
            "has_name": name is not None,
            "entity_type": entity_type,
            "category_key": category_key,
            "category": category,
            "category_group": group,
            "subcategory": None,
            "brand_name": None,
            "brand_wikidata": None,
            "operator": None,
            "wikidata": None,
            "country_code": country_code.upper() if country_code else None,
            "admin1": geography["admin1"],
            "admin2": geography["admin2"],
            "city": geography["city"] or (row["search_city"] if precision == "exact" else None),
            "city_id": geography["city_id"],
            "locality": geography["locality"] or (row["search_area"] if precision == "exact" else None),
            "locality_distance_km": geography["locality_distance_km"],
            "location_precision": precision,
            "postal_code": None,
            "street": None,
            "housenumber": None,
            "address": (row["address"] or "").strip() or None,
            "latitude": row["latitude"],
            "longitude": row["longitude"],
            "website": url,
            "normalized_domain": domain,
            "tld": tld,
            "domain_is_platform": is_platform if domain else None,
            "phone": row["phone"],
            "normalized_phone": normalize_phone(row["phone"], country_code or scope_country),
            "email": None,
            "social_links": None,
            "opening_hours": parse_google_hours(row["hours"]),
            # Sponsored listings are adverts placed by the business itself: real, but less certain to be local.
            "source_confidence": 0.8 if row["sponsored_only"] else 0.9,
            "source_updated_at": row["last_seen_at"],
            "source_version": f"artifact-{row['artifact_id']}",
            "artifact_id": row["artifact_id"],
            "bronze_version_id": context["bronze_version_ids"].get(row["artifact_id"]),
            "pipeline_run_id": context["run_id"],
            "transformation_version": context["transformation_version"],
            "ingested_at": context["now"],
            "attributes": attributes,
        }
        for key, value in values.items():
            places[key].append(value)
    return places, rejects, stats


def transform(ctx: StageContext, bronze: list[dict], scope_country: str,
              transformation_version: str) -> tuple[dict[str, dict], dict]:
    partition = {"source": SOURCE, "country": scope_country}
    geo, geo_version_ids = load_geo_index(scope_country)
    inputs = ([{"type": "dataset_version", "dataset_id": "bronze.google_places", "id": v["id"]} for v in bronze]
              + [{"type": "dataset_version", "dataset_id": "geography", "id": v} for v in geo_version_ids])
    versions = {"transformation": transformation_version, "geography": geo_version_ids}
    context = {"run_id": ctx.run_id, "transformation_version": transformation_version,
               "bronze_version_ids": {int(v["partition"]["artifact_id"]): v["id"] for v in bronze},
               "now": datetime.now(timezone.utc).replace(microsecond=0)}
    totals = {"observations": 0, "unique_places": 0, "excluded_non_business": 0, "outside_country": 0}

    con = duck()
    try:
        source = parquet_source(dataset_files("bronze.google_places", versions=bronze))
        totals["observations"] = con.execute(f"SELECT count(*) FROM {source}").fetchone()[0]
        con.execute(f"CREATE TEMP TABLE merged AS {stacked_points_sql(f'({merge_sql(source)})')}")
        totals["unique_places"] = con.execute("SELECT count(*) FROM merged").fetchone()[0]
        unidentified = con.execute(unidentified_sql(source)).fetchall()
        reader = con.execute("SELECT * FROM merged ORDER BY place_key").to_arrow_reader(BATCH_ROWS)
        with DatasetVersionWriter(ctx.storage, "silver.places", partition, PLACES_SCHEMA, run_id=ctx.run_id,
                                  versions=versions, inputs=inputs) as places_writer, \
             DatasetVersionWriter(ctx.storage, "silver.rejects", partition, REJECTS_SCHEMA, run_id=ctx.run_id,
                                  versions=versions, inputs=inputs) as rejects_writer:
            missing = empty_columns(REJECTS_SCHEMA)
            for name, lat, lon, record, artifact_id in unidentified:
                _reject(missing, "missing_place_id", None, name, lat, lon, record, artifact_id, context)
            rejects_writer.write_batch(pa.table(missing, schema=REJECTS_SCHEMA))
            done = 0
            for batch in reader:
                places, rejects, stats = normalize_batch(batch.to_pylist(), geo=geo, scope_country=scope_country,
                                                         context=context)
                places_writer.write_batch(pa.table(places, schema=PLACES_SCHEMA))
                rejects_writer.write_batch(pa.table(rejects, schema=REJECTS_SCHEMA))
                done += batch.num_rows
                for key in ("excluded_non_business", "outside_country"):
                    totals[key] += stats[key]
                ctx.progress(done, totals["unique_places"], "places", "merging and normalizing places")
    finally:
        con.close()

    totals.update(places=places_writer.row_count, rejected=rejects_writer.row_count,
                  duplicate_observations=totals["observations"] - totals["unique_places"] - len(unidentified),
                  geography=bool(geo))
    return {"silver.places": places_writer.version, "silver.rejects": rejects_writer.version}, totals
