"""OSM Bronze -> Silver (places, rejects, administrative boundaries).

Set-based work (business candidate selection, way/relation centroids, boundary
rings) runs in DuckDB over the Bronze Parquet; per-record normalization runs in
Python on bounded Arrow batches. Nothing here re-reads the original PBF.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone

import pyarrow as pa
import shapely

from ...engine.context import StageContext
from ...geo import Boundary, GeoIndex, Place, polygon_from_rings
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
from ...silver_contract import BOUNDARIES_SCHEMA, GEO_PLACES_SCHEMA, PLACES_SCHEMA, REJECTS_SCHEMA, load_geo_index, to_table
from ...taxonomy import OSM_BUSINESS_TOURISM, OSM_EXCLUDED_AMENITIES, classify_osm

BATCH_ROWS = 50_000
TYPE_NAMES = {"n": "node", "w": "way", "r": "relation"}

def business_predicate(tags: str = "tags") -> str:
    """SQL equivalent of taxonomy.is_osm_business (a test keeps them in sync)."""
    excluded = ", ".join(f"'{v}'" for v in sorted(OSM_EXCLUDED_AMENITIES))
    tourism = ", ".join(f"'{v}'" for v in sorted(OSM_BUSINESS_TOURISM))
    keys = " OR ".join(f"map_contains({tags}, '{k}')" for k in ("shop", "office", "craft", "healthcare",
                                                                "industrial", "company"))
    return f"""(
        coalesce({tags}['shop'], '') <> 'vacant' AND (
            {keys}
            OR (map_contains({tags}, 'amenity') AND {tags}['amenity'] NOT IN ({excluded}))
            OR {tags}['tourism'] IN ({tourism})
            OR {tags}['man_made'] = 'works'
        )
    )"""


def _register_bronze(con, bronze_versions: dict[str, dict]) -> None:
    for dataset_id, view in [("bronze.osm_nodes", "nodes"), ("bronze.osm_ways", "ways"),
                             ("bronze.osm_relations", "relations")]:
        con.execute(f"CREATE VIEW {view} AS SELECT * FROM {parquet_source(version_files(bronze_versions[dataset_id]))}")


# ---------------------------------------------------------------- geography

def build_geography(con, ctx: StageContext) -> tuple[list[Boundary], list[Place], list[dict]]:
    con.execute("""
        CREATE TEMP TABLE boundary_rel AS
        SELECT id, CAST(tags['admin_level'] AS INTEGER) AS level,
               coalesce(tags['name:en'], tags['name']) AS name, tags['name'] AS name_local,
               upper(coalesce(tags['ISO3166-1:alpha2'], tags['ISO3166-1'])) AS iso, members
        FROM relations
        WHERE tags['boundary'] = 'administrative' AND tags['admin_level'] IN ('2', '4', '6')
          AND coalesce(tags['name:en'], tags['name']) IS NOT NULL
    """)
    con.execute("""
        CREATE TEMP TABLE boundary_members AS
        SELECT DISTINCT r.id AS rel_id, m.m.ref AS way_id
        FROM boundary_rel r CROSS JOIN unnest(r.members) AS m(m) WHERE m.m.type = 'w'
    """)
    ways = con.execute("""
        WITH refs AS (
            SELECT w.id AS way_id, u.ref, u.pos
            FROM ways w CROSS JOIN unnest(w.node_refs) WITH ORDINALITY AS u(ref, pos)
            WHERE w.id IN (SELECT way_id FROM boundary_members)
        )
        SELECT r.way_id, list([n.longitude, n.latitude] ORDER BY r.pos) AS coords
        FROM refs r JOIN nodes n ON n.id = r.ref
        WHERE n.latitude IS NOT NULL
        GROUP BY r.way_id
    """).fetchall()
    way_coords = {way_id: [tuple(c) for c in coords] for way_id, coords in ways}
    members: dict[int, list[int]] = {}
    for rel_id, way_id in con.execute("SELECT rel_id, way_id FROM boundary_members").fetchall():
        members.setdefault(rel_id, []).append(way_id)

    boundaries: list[Boundary] = []
    rows: list[dict] = []
    for rel_id, level, name, name_local, iso, _ in con.execute("SELECT * FROM boundary_rel ORDER BY level").fetchall():
        rings = [way_coords[w] for w in members.get(rel_id, []) if w in way_coords]
        geometry = polygon_from_rings(rings)
        if geometry is None:
            continue                                   # incomplete outside the extract
        boundaries.append(Boundary(level, name, iso if level == 2 else None, geometry))
        rows.append({"rel_id": rel_id, "level": level, "name": name, "name_local": name_local, "iso": iso,
                     "geometry": geometry})

    # Provinces/districts inherit the country they lie in.
    countries = [b for b in boundaries if b.level == 2]
    resolved = []
    for boundary, row in zip(boundaries, rows):
        code = boundary.country_code
        if code is None and countries:
            point = shapely.point_on_surface(boundary.geometry)
            code = next((c.country_code for c in countries if c.geometry.contains(point)), None)
        resolved.append(Boundary(boundary.level, boundary.name, code, boundary.geometry))
        row["iso"] = code

    place_rows = con.execute("""
        SELECT id, coalesce(tags['name:en'], tags['name']), tags['place'], latitude, longitude,
               TRY_CAST(regexp_replace(coalesce(tags['population'], ''), '[^0-9]', '', 'g') AS BIGINT)
        FROM nodes
        WHERE tags['place'] IN ('city', 'town') AND map_contains(tags, 'name') AND latitude IS NOT NULL
    """).fetchall()
    places = [Place(name, kind, lat, lon, population) for _, name, kind, lat, lon, population in place_rows]
    return resolved, places, rows, place_rows


def write_boundaries(rows: list[dict], ctx: StageContext, artifact: dict, partition: dict, versions: dict,
                     inputs: list[dict]) -> dict:
    with DatasetVersionWriter(ctx.storage, "silver.geo_boundaries", partition, BOUNDARIES_SCHEMA, run_id=ctx.run_id,
                              versions=versions, inputs=inputs) as writer:
        if rows:
            geometries = [r["geometry"] for r in rows]
            # Approximate area: degrees^2 scaled by latitude; adequate for ranking/size display.
            areas = []
            for g in geometries:
                centroid = shapely.centroid(g)
                areas.append(round(g.area * 111.32 * 111.32 * math.cos(math.radians(centroid.y)), 1))
            writer.write_batch(pa.table({
                "boundary_id": [f"osm:r{r['rel_id']}" for r in rows],
                "source": ["osm"] * len(rows),
                "admin_level": pa.array([r["level"] for r in rows], pa.int16()),
                "name": [r["name"] for r in rows],
                "name_local": [r["name_local"] for r in rows],
                "country_code": [r["iso"] for r in rows],
                "area_km2": areas,
                "geometry_wkb": pa.array(shapely.to_wkb(geometries).tolist(), pa.binary()),
                "artifact_id": pa.array([artifact["id"]] * len(rows), pa.int64()),
                "pipeline_run_id": pa.array([ctx.run_id] * len(rows), pa.int64()),
            }, schema=BOUNDARIES_SCHEMA))
    return writer.version


def write_geo_places(place_rows: list[tuple], ctx: StageContext, artifact: dict, partition: dict, versions: dict,
                     inputs: list[dict]) -> dict:
    """Cities and towns as a shared dataset, so other sources reuse the same city assignment."""
    with DatasetVersionWriter(ctx.storage, "silver.geo_places", partition, GEO_PLACES_SCHEMA, run_id=ctx.run_id,
                              versions=versions, inputs=inputs) as writer:
        if place_rows:
            writer.write_batch(to_table({
                "place_id": [f"osm:n{r[0]}" for r in place_rows], "source": ["osm"] * len(place_rows),
                "name": [r[1] for r in place_rows], "kind": [r[2] for r in place_rows],
                "latitude": [r[3] for r in place_rows], "longitude": [r[4] for r in place_rows],
                "population": [r[5] for r in place_rows],
                "artifact_id": [artifact["id"]] * len(place_rows),
                "pipeline_run_id": [ctx.run_id] * len(place_rows),
            }, GEO_PLACES_SCHEMA))
    return writer.version


SOCIAL_TAGS = {"facebook": "facebook.com", "instagram": "instagram.com", "twitter": "twitter.com",
               "linkedin": "linkedin.com", "youtube": "youtube.com", "tiktok": "tiktok.com"}


def social_links(tags: dict) -> list[str] | None:
    links = []
    for network, host in SOCIAL_TAGS.items():
        for key in (f"contact:{network}", network):
            value = (tags.get(key) or "").strip()
            if value:
                links.append(value if value.startswith("http") else f"https://{host}/{value.lstrip('@/')}")
                break
    return links or None


# ---------------------------------------------------------------- places

def candidates_sql() -> str:
    predicate = business_predicate()
    return f"""
        WITH node_c AS (
            SELECT 'n' AS t, id, tags, version, timestamp, latitude, longitude, 1 AS refs, 1 AS resolved
            FROM nodes WHERE cardinality(tags) > 0 AND {predicate}
        ),
        way_c AS (SELECT id, tags, version, timestamp, node_refs FROM ways WHERE {predicate}),
        way_pts AS (
            SELECT w.id, avg(n.latitude) AS latitude, avg(n.longitude) AS longitude,
                   count(*) AS refs, count(n.latitude) AS resolved
            FROM way_c w CROSS JOIN unnest(w.node_refs) AS u(ref)
            LEFT JOIN nodes n ON n.id = u.ref
            GROUP BY w.id
        ),
        rel_c AS (SELECT id, tags, version, timestamp, members FROM relations WHERE {predicate}),
        rel_refs AS (
            SELECT r.id AS rel_id, m.m.type AS type, m.m.ref AS ref
            FROM rel_c r CROSS JOIN unnest(r.members) AS m(m) WHERE m.m.type IN ('n', 'w')
        ),
        rel_nodes AS (
            SELECT rel_id, ref AS node_id FROM rel_refs WHERE type = 'n'
            UNION ALL
            SELECT rr.rel_id, u.ref FROM rel_refs rr JOIN ways w ON w.id = rr.ref AND rr.type = 'w'
            CROSS JOIN unnest(w.node_refs) AS u(ref)
        ),
        rel_pts AS (
            SELECT rn.rel_id AS id, avg(n.latitude) AS latitude, avg(n.longitude) AS longitude,
                   count(*) AS refs, count(n.latitude) AS resolved
            FROM rel_nodes rn LEFT JOIN nodes n ON n.id = rn.node_id GROUP BY rn.rel_id
        )
        SELECT * FROM node_c
        UNION ALL
        SELECT 'w', w.id, w.tags, w.version, w.timestamp, p.latitude, p.longitude, coalesce(p.refs, 0),
               coalesce(p.resolved, 0)
        FROM way_c w LEFT JOIN way_pts p USING (id)
        UNION ALL
        SELECT 'r', r.id, r.tags, r.version, r.timestamp, p.latitude, p.longitude, coalesce(p.refs, 0),
               coalesce(p.resolved, 0)
        FROM rel_c r LEFT JOIN rel_pts p USING (id)
    """


def _address(tags: dict) -> str | None:
    parts = [tags.get("addr:housenumber"), tags.get("addr:street"), tags.get("addr:suburb"), tags.get("addr:city"),
             tags.get("addr:postcode")]
    text = ", ".join(p.strip() for p in parts if p and p.strip())
    return text or None


def normalize_batch(batch: pa.RecordBatch, *, geo: GeoIndex, scope_country: str, context: dict) \
        -> tuple[dict[str, list], dict[str, list]]:
    rows = batch.to_pylist()
    places = {f.name: [] for f in PLACES_SCHEMA}
    rejects = {f.name: [] for f in REJECTS_SCHEMA}

    valid_rows = []
    for row in rows:
        tags = dict(row["tags"])
        record_id = f"{row['t']}{row['id']}"
        name = (tags.get("name:en") or tags.get("name") or "").strip() or None
        lat, lon = row["latitude"], row["longitude"]
        reason = None
        if row["t"] != "n" and row["resolved"] == 0:
            reason = "missing_coordinates"                 # members outside the extract
        elif not valid_coordinates(lat, lon):
            reason = "invalid_coordinates"
        if reason:
            for key, value in [("source", "osm"), ("source_record_id", record_id), ("reason", reason), ("name", name),
                               ("latitude", lat), ("longitude", lon),
                               ("attributes", json.dumps(tags, ensure_ascii=False)),
                               ("artifact_id", context["artifact_id"]), ("pipeline_run_id", context["run_id"]),
                               ("rejected_at", context["now"])]:
                rejects[key].append(value)
            continue
        valid_rows.append((row, tags, record_id, name))

    geo_values = geo.assign([r[0]["latitude"] for r in valid_rows], [r[0]["longitude"] for r in valid_rows])

    for index, (row, tags, record_id, name) in enumerate(valid_rows):
        category_key, category, group, entity_type = classify_osm(tags)
        country_code = geo_values["country_code"][index] or None
        url, domain, tld, is_platform = normalize_website(tags.get("website") or tags.get("contact:website")
                                                          or tags.get("url"))
        raw_phone = first_value(tags.get("phone") or tags.get("contact:phone") or tags.get("mobile")
                                or tags.get("contact:mobile"))
        timestamp = row["timestamp"]
        values = {
            "silver_id": f"osm:{record_id}",
            "source": "osm",
            "source_record_id": record_id,
            "source_entity_type": TYPE_NAMES[row["t"]],
            "name": name,
            "name_local": (tags.get("name") or "").strip() or None,
            "normalized_name": normalize_name(name),
            "name_key": name_key(name),
            "has_name": name is not None,
            "entity_type": entity_type,
            "category_key": category_key,
            "category": category,
            "category_group": group,
            "subcategory": first_value(tags.get("cuisine") or tags.get("healthcare:speciality")
                                       or tags.get("vending") or tags.get("product")),
            "brand_name": (tags.get("brand:en") or tags.get("brand") or "").strip() or None,
            "brand_wikidata": tags.get("brand:wikidata"),
            "operator": (tags.get("operator:en") or tags.get("operator") or "").strip() or None,
            "wikidata": tags.get("wikidata"),
            "country_code": country_code,
            "admin1": geo_values["admin1"][index],
            "admin2": geo_values["admin2"][index],
            "city": geo_values["city"][index],
            "locality": geo_values["locality"][index],
            "locality_distance_km": geo_values["locality_distance_km"][index],
            "postal_code": first_value(tags.get("addr:postcode")),
            "street": first_value(tags.get("addr:street")),
            "housenumber": first_value(tags.get("addr:housenumber")),
            "address": _address(tags),
            "latitude": row["latitude"],
            "longitude": row["longitude"],
            "website": url,
            "normalized_domain": domain,
            "tld": tld,
            "domain_is_platform": is_platform if domain else None,
            "phone": raw_phone,
            "normalized_phone": normalize_phone(raw_phone, country_code or scope_country),
            "email": normalize_email(tags.get("email") or tags.get("contact:email")),
            "social_links": social_links(tags),
            "opening_hours": tags.get("opening_hours"),
            "source_confidence": None,                       # OSM has no per-record confidence
            "source_updated_at": timestamp.replace(tzinfo=timezone.utc) if timestamp and timestamp.tzinfo is None
            else timestamp,
            "source_version": context["source_version"],
            "artifact_id": context["artifact_id"],
            "bronze_version_id": context["bronze_version_ids"][row["t"]],
            "pipeline_run_id": context["run_id"],
            "transformation_version": context["transformation_version"],
            "ingested_at": context["now"],
            "attributes": json.dumps(tags, ensure_ascii=False),
        }
        for key, value in values.items():
            places[key].append(value)
    return places, rejects


def transform(ctx: StageContext, artifact: dict, bronze_versions: dict[str, dict], scope_country: str,
              transformation_version: str) -> tuple[dict[str, dict], dict]:
    partition = {"source": "osm", "country": scope_country}
    versions: dict = {"transformation": transformation_version}
    inputs = [{"type": "artifact", "id": artifact["id"]}] + [
        {"type": "dataset_version", "dataset_id": dataset_id, "id": v["id"]} for dataset_id, v in bronze_versions.items()
    ]
    con = duck()
    try:
        _register_bronze(con, bronze_versions)
        ctx.progress(0, None, "records", "building administrative boundaries", force=True)
        boundaries, places, boundary_rows, place_rows = build_geography(con, ctx)
        boundary_version = write_boundaries(boundary_rows, ctx, artifact, partition, versions, inputs)
        geo_places_version = write_geo_places(place_rows, ctx, artifact, partition, versions, inputs)
        # Places use the shared geography: these boundaries plus the preferred gazetteer (GeoNames when loaded).
        geo, geo_version_ids = load_geo_index(scope_country)
        geo = geo or GeoIndex(boundaries, places)
        place_versions = {**versions, "geography": geo_version_ids}
        place_inputs = inputs + [{"type": "dataset_version", "dataset_id": "geography", "id": v}
                                 for v in geo_version_ids
                                 if v not in (boundary_version["id"], geo_places_version["id"])]

        context = {
            "artifact_id": artifact["id"], "run_id": ctx.run_id, "source_version": artifact["source_version"],
            "transformation_version": transformation_version,
            "now": datetime.now(timezone.utc).replace(microsecond=0),
            "bronze_version_ids": {"n": bronze_versions["bronze.osm_nodes"]["id"],
                                   "w": bronze_versions["bronze.osm_ways"]["id"],
                                   "r": bronze_versions["bronze.osm_relations"]["id"]},
        }

        ctx.progress(0, None, "records", "selecting business candidates", force=True)
        # Materialize first (spills to disk if large): streaming this query's result directly through
        # the Arrow reader stalls in DuckDB 1.5.x, while scanning a table streams reliably.
        con.execute(f"CREATE TEMP TABLE candidates AS {candidates_sql()}")
        reader = con.execute("SELECT * FROM candidates").to_arrow_reader(BATCH_ROWS)
        processed = 0
        with DatasetVersionWriter(ctx.storage, "silver.places", partition, PLACES_SCHEMA, run_id=ctx.run_id,
                                  versions=place_versions, inputs=place_inputs) as places_writer, \
             DatasetVersionWriter(ctx.storage, "silver.rejects", partition, REJECTS_SCHEMA, run_id=ctx.run_id,
                                  versions=place_versions, inputs=place_inputs) as rejects_writer:
            for batch in reader:
                place_columns, reject_columns = normalize_batch(batch, geo=geo, scope_country=scope_country,
                                                                context=context)
                places_writer.write_batch(pa.table(place_columns, schema=PLACES_SCHEMA))
                rejects_writer.write_batch(pa.table(reject_columns, schema=REJECTS_SCHEMA))
                processed += batch.num_rows
                ctx.progress(processed, None, "records", "normalizing places")
    finally:
        con.close()

    stats = {"candidates": processed, "places": places_writer.row_count, "rejected": rejects_writer.row_count,
             "boundaries": len(boundary_rows), "populated_places": len(places)}
    return {"silver.places": places_writer.version, "silver.rejects": rejects_writer.version,
            "silver.geo_boundaries": boundary_version, "silver.geo_places": geo_places_version}, stats
