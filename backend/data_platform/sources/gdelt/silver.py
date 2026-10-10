"""GDELT Bronze -> silver.org_mentions: one row per organization (normalized name) in the retention window."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pyarrow as pa

from ...engine.context import StageContext
from ...lake.reader import dataset_files, duck, parquet_source
from ...lake.writer import DatasetVersionWriter, copy_query_into
from ...normalize import name_key

# GDELT's entity extraction also tags outlets, wire services and generic bodies; they are not discovery targets.
NOISE = {"associated press", "reuters", "agence france presse", "afp", "facebook", "twitter", "instagram",
         "youtube", "google", "whatsapp", "united nations", "terms of service", "privacy policy", "bbc",
         "cnn", "al jazeera", "xinhua", "press trust of india", "dawn", "geo news", "ary news", "the express tribune"}


def _title(value: str | None) -> str | None:
    if not value:
        return value
    return " ".join(w if w.isupper() else w[:1].upper() + w[1:] for w in value.split(" "))


def transform(ctx: StageContext, country: str, bronze: list[dict], signature: dict, transformation_version: str,
              fips: str):
    """`fips`: the country's code in GDELT locations (FIPS 10-4, e.g. PK; Germany is GM)."""
    partition = {"source": "gdelt", "country": country}
    since = datetime.now(timezone.utc) - timedelta(days=signature["retention_days"])
    con = duck()
    try:
        con.create_function("name_key", name_key, ["VARCHAR"], "VARCHAR", null_handling="special")
        con.create_function("title_case", _title, ["VARCHAR"], "VARCHAR", null_handling="special")
        source = parquet_source(dataset_files("bronze.gdelt_gkg", versions=bronze))
        noise = ", ".join("'" + n.replace("'", "''") + "'" for n in sorted(NOISE))
        con.execute(f"""
            CREATE TEMP TABLE records AS
            SELECT DISTINCT ON (document_url, gkg_record_id) gkg_record_id, document_url, source_name,
                   published_at, organizations, themes, locations,
                   TRY_CAST(split_part(tone, ',', 1) AS DOUBLE) AS tone
            FROM {source}
            WHERE published_at >= TIMESTAMPTZ '{since.isoformat()}' AND organizations IS NOT NULL
        """)
        records = con.execute("SELECT count(*) FROM records").fetchone()[0]
        ctx.progress(0, None, "records", f"aggregating organizations in {records:,} articles", force=True)
        # GDELT tags governments by country name ("United States"); those are not organizations to discover.
        country_files = dataset_files("bronze.geonames_countries")
        names = [r[0] for r in con.execute(f"SELECT DISTINCT country FROM {parquet_source(country_files)}").fetchall()] \
            if country_files else []
        con.register("country_keys", pa.table({"key": sorted({k for k in map(name_key, names) if k})},
                                              schema=pa.schema([("key", pa.string())])))
        con.execute(f"""
            CREATE TEMP TABLE mentions AS
            SELECT r.*, trim(o.org) AS surface, name_key(trim(o.org)) AS key
            FROM records r, unnest(string_split(r.organizations, ';')) AS o(org)
            WHERE trim(o.org) <> '' AND trim(o.org) NOT IN ({noise})
        """)
        con.execute("DELETE FROM mentions WHERE key IN (SELECT key FROM country_keys)")
        with DatasetVersionWriter(ctx.storage, "silver.org_mentions", partition, None, run_id=ctx.run_id,
                                  versions=signature,
                                  inputs=[{"type": "dataset_version", "dataset_id": "bronze.gdelt_gkg", "id": v["id"]}
                                          for v in bronze]) as writer:
            copy_query_into(writer, con, f"""
                WITH themes AS (
                    SELECT key, list(theme ORDER BY n DESC, theme)[1:5] AS top_themes FROM (
                        SELECT key, t.theme, count(*) AS n
                        FROM mentions, unnest(string_split(themes, ';')) AS t(theme)
                        WHERE themes IS NOT NULL GROUP BY 1, 2) WHERE theme <> '' GROUP BY key
                ),
                places AS (
                    SELECT key, list(place ORDER BY n DESC, place)[1:3] AS top_locations FROM (
                        SELECT key, split_part(location, '#', 2) AS place, count(*) AS n
                        FROM mentions, unnest(string_split(locations, ';')) AS l(location)
                        WHERE split_part(location, '#', 3) = '{fips}'
                        GROUP BY 1, 2) WHERE place <> '' GROUP BY key
                )
                SELECT m.key AS name_key, 'gdelt' AS source, '{country}' AS country_code,
                       title_case(mode(m.surface)) AS name, list(DISTINCT m.surface)[1:10] AS surface_forms,
                       count(*) AS mentions, count(DISTINCT m.document_url) AS articles,
                       count(DISTINCT m.source_name) AS outlets,
                       min(m.published_at) AS first_seen, max(m.published_at) AS last_seen,
                       round(avg(m.tone), 3) AS avg_tone, t.top_themes, p.top_locations,
                       list(DISTINCT m.document_url)[1:5] AS sample_urls,
                       count(DISTINCT m.published_at::DATE)::INTEGER AS days_covered,
                       {ctx.run_id}::BIGINT AS pipeline_run_id, '{transformation_version}' AS transformation_version,
                       now()::TIMESTAMPTZ AS ingested_at
                FROM mentions m LEFT JOIN themes t USING (key) LEFT JOIN places p USING (key)
                WHERE m.key IS NOT NULL
                GROUP BY m.key, t.top_themes, p.top_locations
            """)
    finally:
        con.close()
    return writer.version, {"records": int(records), "organizations": writer.version["row_count"],
                            "windows": len(bronze)}
