"""GeoNames Bronze -> Silver: gazetteer (silver.geo_places), postal codes, and notable facilities (silver.places).

The gazetteer is written first; facilities are then placed with the shared geography,
which from now on prefers these GeoNames places for city / locality.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ... import repository
from ...engine.context import StageContext
from ...lake.reader import duck, parquet_source, version_files
from ...lake.writer import DatasetVersionWriter, copy_query_into
from ...normalize import name_key, normalize_name
from ...silver_contract import PLACES_SCHEMA, REJECTS_SCHEMA, empty_columns, geography_version_ids, load_geo_index, to_table
from ...taxonomy import GEONAMES_EXCLUDED_BY_DEFAULT, GEONAMES_FEATURES
from ..common import silver_is_current

SILVER_DATASETS = ["silver.places", "silver.rejects", "silver.geo_places", "silver.postal_codes"]
# Historical / abandoned / destroyed settlements are not part of today's geography.
EXCLUDED_PLACE_CODES = ("PPLH", "PPLQ", "PPLW", "PPLCH")
# Pseudo-languages in the alternate names file that carry identifiers, not names.
NON_NAME_LANGUAGES = ("link", "wkdt", "post", "iata", "icao", "faac", "abbr", "unlc", "tcid", "fr_1793")


def place_kind_sql() -> str:
    return """CASE WHEN g.feature_code IN ('PPLC', 'PPLA', 'PPLA2') OR g.population >= 50000 THEN 'city'
                   WHEN g.feature_code IN ('PPLA3', 'PPLA4', 'PPLA5') OR g.population >= 5000 THEN 'town'
                   WHEN g.feature_code = 'PPLX' THEN 'suburb'
                   ELSE 'village' END"""


def _views(con, bronze: dict[str, dict]) -> None:
    for dataset_id, view in [("bronze.geonames_places", "features"), ("bronze.geonames_alternate_names", "alt"),
                             ("bronze.geonames_admin_codes", "admin"), ("bronze.geonames_postal_codes", "postal")]:
        con.execute(f"CREATE VIEW {view} AS SELECT * FROM {parquet_source(version_files(bronze[dataset_id]))}")
    languages = ", ".join(f"'{lang}'" for lang in NON_NAME_LANGUAGES)
    con.execute(f"""
        CREATE TEMP TABLE names AS
        SELECT geonameid,
               arg_max(alternate_name, (coalesce(is_preferred_name, '') = '1')::INT * 10
                                       + (coalesce(is_short_name, '') = '1')::INT)
                   FILTER (WHERE isolanguage = 'ur') AS name_ur,
               arg_max(alternate_name, (coalesce(is_preferred_name, '') = '1')::INT)
                   FILTER (WHERE isolanguage = 'en') AS name_en,
               any_value(alternate_name) FILTER (WHERE isolanguage = 'wkdt') AS wikidata,
               any_value(alternate_name) FILTER (WHERE isolanguage = 'link'
                                                 AND alternate_name LIKE '%en.wikipedia.org%') AS wikipedia,
               list(DISTINCT alternate_name) FILTER (WHERE coalesce(isolanguage, '') NOT IN ({languages})
                                                     AND coalesce(is_historic, '') <> '1') AS alternate_names
        FROM alt GROUP BY geonameid
    """)
    con.execute("""
        CREATE TEMP TABLE admin_names AS
        SELECT code, coalesce(asciiname, name) AS name, admin_level FROM admin
    """)


def transform(ctx: StageContext, artifact: dict, bronze: dict[str, dict], transformation_version: str):
    country = artifact["country"]
    partition = {"source": "geonames", "country": country}
    bronze_ids = {v["id"] for v in bronze.values()}
    current = silver_is_current(ctx, SILVER_DATASETS, partition, bronze_ids, transformation_version)
    if current:
        # The gazetteer is this source's own geography; facilities must also be placed with today's boundaries.
        places = repository.get_version(current["silver.places"])
        if sorted(places["versions"].get("geography") or []) == geography_version_ids(country):
            return None, {"current": current, "places": places["row_count"]}

    versions = {"transformation": transformation_version}
    inputs = [{"type": "artifact", "id": artifact["id"]}] + [
        {"type": "dataset_version", "dataset_id": d, "id": v["id"]} for d, v in bronze.items()]
    common = dict(run_id=ctx.run_id, versions=versions, inputs=inputs)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    written = {}
    con = duck()
    try:
        _views(con, bronze)
        ctx.progress(0, None, "records", "building the gazetteer", force=True)
        with DatasetVersionWriter(ctx.storage, "silver.geo_places", partition, None, **common) as writer:
            copy_query_into(writer, con, f"""
                SELECT 'geonames:' || g.geonameid AS place_id, 'geonames' AS source,
                       coalesce(nullif(g.asciiname, ''), g.name) AS name, {place_kind_sql()} AS kind,
                       g.latitude, g.longitude, nullif(g.population, 0) AS population,
                       {artifact['id']}::BIGINT AS artifact_id, {ctx.run_id}::BIGINT AS pipeline_run_id,
                       coalesce(n.name_ur, CASE WHEN g.name <> g.asciiname THEN g.name END) AS name_local,
                       n.alternate_names[1:30] AS alternate_names, g.feature_code, g.country_code,
                       a1.name AS admin1, a2.name AS admin2, g.timezone,
                       coalesce(g.elevation, nullif(g.dem, -9999))::INTEGER AS elevation_m,
                       g.modification_date AS source_updated_at, n.wikidata
                FROM features g
                LEFT JOIN names n USING (geonameid)
                LEFT JOIN admin_names a1 ON a1.code = g.country_code || '.' || g.admin1_code
                LEFT JOIN admin_names a2 ON a2.code = g.country_code || '.' || g.admin1_code || '.' || g.admin2_code
                WHERE g.feature_class = 'P' AND g.feature_code NOT IN {EXCLUDED_PLACE_CODES}
                  AND g.country_code = '{country}'
            """)
        written["silver.geo_places"] = writer.version

        with DatasetVersionWriter(ctx.storage, "silver.postal_codes", partition, None, **common) as writer:
            copy_query_into(writer, con, f"""
                SELECT postal_code, 'geonames' AS source, country_code, place_name, admin1_name AS admin1,
                       admin2_name AS admin2, admin3_name AS admin3, latitude, longitude, accuracy,
                       {artifact['id']}::BIGINT AS artifact_id, {ctx.run_id}::BIGINT AS pipeline_run_id
                FROM postal WHERE country_code = '{country}'
            """)
        written["silver.postal_codes"] = writer.version

        codes = tuple(c for c in GEONAMES_FEATURES if c not in GEONAMES_EXCLUDED_BY_DEFAULT)
        facilities = con.execute(f"""
            SELECT g.geonameid, g.name, g.asciiname, g.latitude, g.longitude, g.feature_class, g.feature_code,
                   g.modification_date, n.name_en, n.name_ur, n.wikidata, n.wikipedia,
                   to_json(struct_pack(geonameid := g.geonameid, name := g.name, feature := g.feature_code,
                                       alternatenames := g.alternatenames, population := g.population,
                                       timezone := g.timezone, wikipedia := n.wikipedia))::VARCHAR AS attributes
            FROM features g LEFT JOIN names n USING (geonameid)
            WHERE g.feature_class = 'S' AND g.feature_code IN {codes} AND g.country_code = '{country}'
            ORDER BY g.geonameid
        """).fetchall()
        feature_count = con.execute("SELECT count(*) FROM features").fetchone()[0]
    finally:
        con.close()

    # The gazetteer just committed is now part of the shared geography.
    geo, geo_ids = load_geo_index(country)
    places = empty_columns(PLACES_SCHEMA)
    context = {"versions": {**versions, "geography": geo_ids},
               "inputs": inputs + [{"type": "dataset_version", "dataset_id": "geography", "id": v} for v in geo_ids]}
    assigned = geo.assign([r[3] for r in facilities], [r[4] for r in facilities]) if geo else None
    for index, (gid, raw_name, ascii_name, lat, lon, fclass, code, modified, name_en, name_ur, wikidata, wikipedia,
                attributes) in enumerate(facilities):
        category, group, entity_type = GEONAMES_FEATURES[code]
        name = (name_en or ascii_name or raw_name or "").strip() or None
        values = {
            "silver_id": f"geonames:{gid}", "source": "geonames", "source_record_id": str(gid),
            "source_entity_type": "feature", "name": name,
            "name_local": name_ur or (raw_name if raw_name and raw_name != name else None),
            "normalized_name": normalize_name(name), "name_key": name_key(name), "has_name": name is not None,
            "entity_type": entity_type, "category_key": code, "category": category, "category_group": group,
            "subcategory": None, "brand_name": None, "brand_wikidata": None, "operator": None,
            "wikidata": wikidata,
            "country_code": (assigned["country_code"][index] if assigned else None) or country,
            "admin1": assigned["admin1"][index] if assigned else None,
            "admin2": assigned["admin2"][index] if assigned else None,
            "city": assigned["city"][index] if assigned else None,
            "locality": assigned["locality"][index] if assigned else None,
            "locality_distance_km": assigned["locality_distance_km"][index] if assigned else None,
            "latitude": lat, "longitude": lon,
            # GeoNames facilities often date from old gazetteers: kept, but below OSM in survivorship and
            # with a modest confidence so an unconfirmed one alone is still promoted (>= the Gold floor).
            "source_confidence": 0.5,
            "source_updated_at": datetime(modified.year, modified.month, modified.day, tzinfo=timezone.utc)
            if modified else None,
            "source_version": artifact["source_version"], "artifact_id": artifact["id"],
            "bronze_version_id": bronze["bronze.geonames_places"]["id"], "pipeline_run_id": ctx.run_id,
            "transformation_version": transformation_version, "ingested_at": now, "attributes": attributes,
        }
        for key in places:
            places[key].append(values.get(key))

    with DatasetVersionWriter(ctx.storage, "silver.places", partition, PLACES_SCHEMA, run_id=ctx.run_id,
                              versions=context["versions"], inputs=context["inputs"]) as writer:
        writer.write_batch(to_table(places, PLACES_SCHEMA))
    written["silver.places"] = writer.version
    with DatasetVersionWriter(ctx.storage, "silver.rejects", partition, REJECTS_SCHEMA, run_id=ctx.run_id,
                              versions=context["versions"], inputs=context["inputs"]) as writer:
        pass                                      # GeoNames records are never rejected; an empty version
    written["silver.rejects"] = writer.version

    stats = {"features": int(feature_count), "gazetteer_places": written["silver.geo_places"]["row_count"],
             "postal_codes": written["silver.postal_codes"]["row_count"], "places": len(facilities),
             "with_wikidata": sum(1 for r in facilities if r[10])}
    return written, stats
