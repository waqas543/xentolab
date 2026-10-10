"""Overture Bronze -> Silver places.

Overture's nested GeoParquet is flattened in DuckDB; the shared normalizers and the
shared geography (silver.geo_boundaries / silver.geo_places, from any source) are
applied in bounded batches. Non-business places (worship, parks, transport hubs,
natural features) are dropped by the shared taxonomy; permanently closed places and
unusable coordinates go to silver.rejects.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pyarrow as pa

from ...engine.context import StageContext
from ...lake.reader import duck, parquet_source, version_files
from ...lake.writer import DatasetVersionWriter
from ...normalize import (
    first_value,
    name_key,
    normalize_email,
    normalize_name,
    normalize_phone,
    normalize_website,
    valid_coordinates,
)
from ...silver_contract import PLACES_SCHEMA, REJECTS_SCHEMA, empty_columns, load_geo_index
from ...taxonomy import classify_overture

BATCH_ROWS = 50_000
LINEAGE_COLUMNS = {"source", "artifact_id", "pipeline_run_id", "ingested_at"}


def flatten_sql(columns: set[str], source: str) -> str:
    """Flatten the columns this release has. Older releases used `categories` instead of `taxonomy`."""
    if "taxonomy" in columns:
        hierarchy, primary = "taxonomy.hierarchy", "taxonomy.primary"
    elif "categories" in columns:
        hierarchy, primary = "[categories.primary]", "categories.primary"
    else:
        hierarchy, primary = "CAST(NULL AS VARCHAR[])", "CAST(NULL AS VARCHAR)"

    def optional(column: str, expression: str, sql_type: str) -> str:
        return expression if column in columns else f"CAST(NULL AS {sql_type})"

    raw_columns = [c for c in sorted(columns) if c not in LINEAGE_COLUMNS | {"geometry"}]
    attributes = "to_json(struct_pack(" + ", ".join(f'"{c}" := "{c}"' for c in raw_columns) + "))::VARCHAR"
    return f"""
        SELECT id,
               names.primary AS name_primary,
               {optional('names', "names.common['en']", 'VARCHAR')} AS name_en,
               {optional('names', "names.common['ur']", 'VARCHAR')} AS name_ur,
               {hierarchy} AS hierarchy, {primary} AS category_primary,
               {optional('confidence', 'confidence', 'DOUBLE')} AS confidence,
               {optional('websites', 'websites', 'VARCHAR[]')} AS websites,
               {optional('socials', 'socials', 'VARCHAR[]')} AS socials,
               {optional('emails', 'emails', 'VARCHAR[]')} AS emails,
               {optional('phones', 'phones', 'VARCHAR[]')} AS phones,
               {optional('brand', 'brand.names.primary', 'VARCHAR')} AS brand_name,
               {optional('brand', 'brand.wikidata', 'VARCHAR')} AS brand_wikidata,
               {optional('addresses', 'addresses[1]', 'STRUCT(freeform VARCHAR, locality VARCHAR, postcode VARCHAR, region VARCHAR, country VARCHAR)')} AS address,
               {optional('operating_status', 'operating_status', 'VARCHAR')} AS operating_status,
               {optional('sources', 'list_max(list_transform(sources, s -> s.update_time))', 'VARCHAR')} AS update_time,
               (bbox.ymin + bbox.ymax) / 2 AS latitude,
               (bbox.xmin + bbox.xmax) / 2 AS longitude,
               {attributes} AS attributes
        FROM {source}
    """


def _timestamp(value: str | None):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)


def _clean(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def normalize_batch(rows: list[dict], *, geo, scope_country: str, context: dict):
    places = empty_columns(PLACES_SCHEMA)
    rejects = empty_columns(REJECTS_SCHEMA)
    stats = {"excluded_non_business": 0, "outside_country": 0}

    kept = []
    for row in rows:
        classification = classify_overture(row["hierarchy"], row["category_primary"])
        if classification is None:
            stats["excluded_non_business"] += 1
            continue
        reason = None
        if (row["operating_status"] or "").lower() == "permanently_closed":
            reason = "permanently_closed"
        elif not valid_coordinates(row["latitude"], row["longitude"]):
            reason = "invalid_coordinates"
        name = _clean(row["name_en"]) or _clean(row["name_primary"])
        if reason:
            for key, value in [("source", "overture"), ("source_record_id", row["id"]), ("reason", reason),
                               ("name", name), ("latitude", row["latitude"]), ("longitude", row["longitude"]),
                               ("attributes", row["attributes"]), ("artifact_id", context["artifact_id"]),
                               ("pipeline_run_id", context["run_id"]), ("rejected_at", context["now"])]:
                rejects[key].append(value)
            continue
        kept.append((row, classification, name))

    geo_values = geo.assign([r[0]["latitude"] for r in kept], [r[0]["longitude"] for r in kept]) if geo else None

    for index, (row, (category_key, category, group, entity_type), name) in enumerate(kept):
        address = row["address"] or {}
        address_country = _clean(address.get("country"))
        country_code = (geo_values["country_code"][index] if geo_values else None) or address_country
        if country_code and country_code.upper() != scope_country:
            stats["outside_country"] += 1          # in the extract by address, but located in another country
            continue
        url, domain, tld, is_platform = normalize_website(first_value(";".join(row["websites"] or [])))
        raw_phone = (row["phones"] or [None])[0]
        local = _clean(row["name_primary"]) if _clean(row["name_primary"]) != name else _clean(row["name_ur"])
        street = _clean(address.get("freeform"))
        address_text = ", ".join(p for p in [street, _clean(address.get("locality")), _clean(address.get("postcode"))] if p)
        values = {
            "silver_id": f"overture:{row['id']}",
            "source": "overture",
            "source_record_id": row["id"],
            "source_entity_type": "place",
            "name": name,
            "name_local": local,
            "normalized_name": normalize_name(name),
            "name_key": name_key(name),
            "has_name": name is not None,
            "entity_type": entity_type,
            "category_key": category_key,
            "category": category,
            "category_group": group,
            "subcategory": (row["hierarchy"] or [None, None, None])[2] if len(row["hierarchy"] or []) > 2 else None,
            "brand_name": _clean(row["brand_name"]),
            "brand_wikidata": _clean(row["brand_wikidata"]),
            "operator": None,
            "wikidata": None,
            "country_code": country_code.upper() if country_code else None,
            "admin1": geo_values["admin1"][index] if geo_values else _clean(address.get("region")),
            "admin2": geo_values["admin2"][index] if geo_values else None,
            "city": (geo_values["city"][index] if geo_values else None) or _clean(address.get("locality")),
            "locality": (geo_values["locality"][index] if geo_values else None) or _clean(address.get("locality")),
            "locality_distance_km": geo_values["locality_distance_km"][index] if geo_values else None,
            "postal_code": _clean(address.get("postcode")),
            "street": street,
            "housenumber": None,
            "address": address_text or None,
            "latitude": row["latitude"],
            "longitude": row["longitude"],
            "website": url,
            "normalized_domain": domain,
            "tld": tld,
            "domain_is_platform": is_platform if domain else None,
            "phone": raw_phone,
            "normalized_phone": normalize_phone(raw_phone, country_code or scope_country),
            "email": normalize_email((row["emails"] or [None])[0]),
            "social_links": row["socials"] or None,
            "opening_hours": None,
            "source_confidence": row["confidence"],
            "source_updated_at": _timestamp(row["update_time"]),
            "source_version": context["source_version"],
            "artifact_id": context["artifact_id"],
            "bronze_version_id": context["bronze_version_id"],
            "pipeline_run_id": context["run_id"],
            "transformation_version": context["transformation_version"],
            "ingested_at": context["now"],
            "attributes": row["attributes"],
        }
        for key, value in values.items():
            places[key].append(value)
    return places, rejects, stats


def transform(ctx: StageContext, artifact: dict, bronze_version: dict, scope_country: str,
              transformation_version: str) -> tuple[dict[str, dict], dict]:
    partition = {"source": "overture", "country": scope_country}
    geo, geo_version_ids = load_geo_index(scope_country)
    inputs = ([{"type": "artifact", "id": artifact["id"]},
               {"type": "dataset_version", "dataset_id": "bronze.overture_places", "id": bronze_version["id"]}]
              + [{"type": "dataset_version", "dataset_id": "geography", "id": v} for v in geo_version_ids])
    versions = {"transformation": transformation_version, "geography": geo_version_ids}
    context = {"artifact_id": artifact["id"], "run_id": ctx.run_id, "source_version": artifact["source_version"],
               "transformation_version": transformation_version, "bronze_version_id": bronze_version["id"],
               "now": datetime.now(timezone.utc).replace(microsecond=0)}
    totals = {"candidates": 0, "excluded_non_business": 0, "outside_country": 0}

    con = duck()
    try:
        source = parquet_source(version_files(bronze_version))
        columns = {row[0] for row in con.execute(f"DESCRIBE SELECT * FROM {source}").fetchall()}
        # Materialize first: streaming complex results through the Arrow reader can stall in DuckDB 1.5.x.
        con.execute(f"CREATE TEMP TABLE flat AS {flatten_sql(columns, source)}")
        total = con.execute("SELECT count(*) FROM flat").fetchone()[0]
        reader = con.execute("SELECT * FROM flat").to_arrow_reader(BATCH_ROWS)
        with DatasetVersionWriter(ctx.storage, "silver.places", partition, PLACES_SCHEMA, run_id=ctx.run_id,
                                  versions=versions, inputs=inputs) as places_writer, \
             DatasetVersionWriter(ctx.storage, "silver.rejects", partition, REJECTS_SCHEMA, run_id=ctx.run_id,
                                  versions=versions, inputs=inputs) as rejects_writer:
            for batch in reader:
                places, rejects, stats = normalize_batch(batch.to_pylist(), geo=geo, scope_country=scope_country,
                                                         context=context)
                places_writer.write_batch(pa.table(places, schema=PLACES_SCHEMA))
                rejects_writer.write_batch(pa.table(rejects, schema=REJECTS_SCHEMA))
                totals["candidates"] += batch.num_rows
                for key in ("excluded_non_business", "outside_country"):
                    totals[key] += stats[key]
                ctx.progress(totals["candidates"], total, "records", "normalizing places")
    finally:
        con.close()

    totals.update(places=places_writer.row_count, rejected=rejects_writer.row_count, geography=bool(geo))
    return {"silver.places": places_writer.version, "silver.rejects": rejects_writer.version}, totals

