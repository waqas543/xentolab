"""ENTITY_RESOLUTION stage: compute candidate matches for a country scope and store them.

Matches live in PostgreSQL (er.entity_matches) because they carry human decisions.
Recomputation updates machine-made matches but never overrides `accepted`/`rejected`.
"""

from __future__ import annotations

import hashlib
import json

from .. import db, repository
from ..engine.context import StageContext
from ..lake.reader import dataset_files, duck, parquet_source
from ..sources.base import StageResult
from .algorithms import ALGORITHMS, ORGANIZATION_LINKING

MATCH_COLUMNS = ["left_id", "right_id", "match_score", "name_score", "domain_score", "address_score", "geo_score",
                 "phone_score", "distance_m", "decision", "evidence"]


def scope_inputs(country: str) -> list[dict]:
    return repository.current_versions("silver.places", {"country": country})


def organization_inputs(country: str) -> list[dict]:
    return repository.current_versions("silver.organizations", {"country": country})


def er_input_ids(country: str) -> list[int]:
    """Silver versions entity resolution for a country depends on (places and organization records)."""
    return sorted(v["id"] for v in scope_inputs(country) + organization_inputs(country))


def er_fingerprint(country: str) -> str:
    """Changes whenever matches for the scope change (machine recompute or human decision)."""
    with db.serving() as conn:
        row = conn.execute("""
            SELECT count(*) AS n, max(updated_at) AS latest,
                   count(*) FILTER (WHERE status IN ('accepted', 'automatic')) AS linking
            FROM er.entity_matches WHERE country_code = %s
        """, [country]).fetchone()
    return hashlib.sha256(json.dumps(row, default=str).encode()).hexdigest()[:16]


def named_places_view(con, files: list[str], country: str) -> None:
    con.execute(f"""
        CREATE OR REPLACE VIEW places AS
        SELECT * FROM {parquet_source(files)}
        WHERE has_name AND (country_code = '{country}' OR country_code IS NULL)
    """)


def resolve(ctx: StageContext) -> StageResult:
    country = ctx.params["country"]
    algorithm = ALGORITHMS[ctx.params["er_algorithm"]]
    silver = scope_inputs(country)
    organizations = organization_inputs(country)
    if not silver:
        return StageResult.skipped(f"No Silver places for {country}")
    state = {"silver_versions": er_input_ids(country), "algorithm": algorithm.version,
             "organization_algorithm": ORGANIZATION_LINKING.version}
    checkpoint = repository.get_checkpoint(f"er:{country}", "state")
    if checkpoint and {k: checkpoint.get(k) for k in state} == state and not ctx.params["rebuild_gold"]:
        ctx.put(er_state=checkpoint)
        return StageResult.skipped("Matches are current for these Silver versions and algorithm",
                                   records_out=checkpoint.get("matches"))

    con = duck()
    try:
        named_places_view(con, dataset_files("silver.places", versions=silver), country)
        places = con.execute("SELECT count(*) FROM places").fetchone()[0]
        ctx.progress(0, None, "pairs", f"blocking and scoring {places:,} named places", force=True)
        con.execute(f"CREATE TEMP TABLE matches AS {algorithm.candidates_sql('places')}")
        rows = con.execute("""
            SELECT left_id, right_id, match_score, name_score, domain_score, address_score, geo_score, phone_score,
                   distance_m, decision,
                   to_json({'blocks': blocks, 'left_group': left_group, 'right_group': right_group})::VARCHAR
            FROM matches
        """).fetchall()
        org_rows = organization_links(con, organizations, ctx) if organizations else []
    finally:
        con.close()

    ctx.progress(len(rows), len(rows), "pairs", "storing matches", force=True)
    with db.serving() as conn, conn.transaction():
        conn.execute("""
            CREATE TEMP TABLE incoming (left_id TEXT, right_id TEXT, match_score FLOAT8, name_score FLOAT8,
                domain_score FLOAT8, address_score FLOAT8, geo_score FLOAT8, phone_score FLOAT8, distance_m FLOAT8,
                decision TEXT, evidence JSONB) ON COMMIT DROP
        """)
        with conn.cursor() as cur, cur.copy(f"COPY incoming ({', '.join(MATCH_COLUMNS)}) FROM STDIN") as copy:
            for row in rows:
                copy.write_row(row)
        conn.execute("""
            INSERT INTO er.entity_matches (left_id, right_id, country_code, match_type, match_score, name_score,
                domain_score, address_score, geo_score, phone_score, distance_m, algorithm_version, status, evidence,
                pipeline_run_id)
            SELECT left_id, right_id, %(country)s, 'location', match_score, name_score, domain_score, address_score,
                   geo_score, phone_score, distance_m, %(algorithm)s, decision, evidence, %(run)s
            FROM incoming
            ON CONFLICT (left_id, right_id, match_type) DO UPDATE SET
                match_score = EXCLUDED.match_score, name_score = EXCLUDED.name_score,
                domain_score = EXCLUDED.domain_score, address_score = EXCLUDED.address_score,
                geo_score = EXCLUDED.geo_score, phone_score = EXCLUDED.phone_score,
                distance_m = EXCLUDED.distance_m, algorithm_version = EXCLUDED.algorithm_version,
                evidence = EXCLUDED.evidence, pipeline_run_id = EXCLUDED.pipeline_run_id,
                status = CASE WHEN er.entity_matches.status IN ('accepted', 'rejected')
                              THEN er.entity_matches.status ELSE EXCLUDED.status END,
                updated_at = CASE WHEN er.entity_matches.status IS DISTINCT FROM EXCLUDED.status
                                    OR er.entity_matches.match_score IS DISTINCT FROM EXCLUDED.match_score
                                  THEN now() ELSE er.entity_matches.updated_at END
        """, {"country": country, "algorithm": algorithm.version, "run": ctx.run_id})
        # Machine matches that no longer hold are removed; human decisions are kept.
        removed = conn.execute("""
            DELETE FROM er.entity_matches m
            WHERE m.country_code = %s AND m.match_type = 'location' AND m.status IN ('automatic', 'suggested')
              AND NOT EXISTS (SELECT 1 FROM incoming i WHERE i.left_id = m.left_id AND i.right_id = m.right_id)
        """, [country]).rowcount
        removed += store_organization_links(conn, org_rows, country, ctx.run_id)
        counts = {r["status"]: r["n"] for r in conn.execute("""
            SELECT status, count(*) AS n FROM er.entity_matches WHERE country_code = %s GROUP BY status
        """, [country]).fetchall()}

    state = {**state, "matches": len(rows), "organization_links": len(org_rows), "fingerprint": er_fingerprint(country)}
    repository.set_checkpoint(f"er:{country}", "state", state, ctx.run_id)
    ctx.put(er_state=state)
    return StageResult(records_in=places, records_out=len(rows),
                       metrics={"named_places": places, "candidate_pairs": len(rows), "removed_stale": removed,
                                "organization_links": len(org_rows),
                                **{f"matches_{k}": v for k, v in counts.items()}, "algorithm": algorithm.version})


def organization_links(con, organizations: list[dict], ctx: StageContext) -> list[tuple]:
    """Place<->organization and organization<->organization links, as rows for er.entity_matches."""
    country = ctx.params["country"]
    con.execute(f"""
        CREATE OR REPLACE VIEW orgs AS
        SELECT * FROM {parquet_source(dataset_files('silver.organizations', versions=organizations))}
        WHERE name IS NOT NULL AND (country_code = '{country}' OR country_code IS NULL)
    """)
    ctx.progress(0, None, "links", "linking organizations to places", force=True)
    algorithm = ORGANIZATION_LINKING
    place_links = con.execute(f"""
        SELECT least(place_id, org_id), greatest(place_id, org_id), match_score, name_score, domain_score,
               distance_m, decision, to_json({{'method': method, 'kind': 'place_organization'}})::VARCHAR
        FROM ({algorithm.place_links_sql('places', 'orgs')})
    """).fetchall()
    org_links = con.execute(f"""
        SELECT left_id, right_id, match_score, NULL, NULL, NULL, decision,
               to_json({{'method': method, 'kind': 'organization_organization'}})::VARCHAR
        FROM ({algorithm.org_links_sql('orgs')})
    """).fetchall()
    return place_links + org_links


def store_organization_links(conn, rows: list[tuple], country: str, run_id: int) -> int:
    """Upsert organization links (match_type 'organization'); human decisions survive, stale machine links go."""
    conn.execute("""
        CREATE TEMP TABLE incoming_org (left_id TEXT, right_id TEXT, match_score FLOAT8, name_score FLOAT8,
            domain_score FLOAT8, distance_m FLOAT8, decision TEXT, evidence JSONB) ON COMMIT DROP
    """)
    with conn.cursor() as cur, cur.copy("COPY incoming_org FROM STDIN") as copy:
        for row in rows:
            copy.write_row(row)
    conn.execute("""
        INSERT INTO er.entity_matches (left_id, right_id, country_code, match_type, match_score, name_score,
            domain_score, distance_m, algorithm_version, status, evidence, pipeline_run_id)
        SELECT left_id, right_id, %(country)s, 'organization', match_score, name_score, domain_score, distance_m,
               %(algorithm)s, decision, evidence, %(run)s
        FROM incoming_org
        ON CONFLICT (left_id, right_id, match_type) DO UPDATE SET
            match_score = EXCLUDED.match_score, name_score = EXCLUDED.name_score,
            domain_score = EXCLUDED.domain_score, distance_m = EXCLUDED.distance_m,
            algorithm_version = EXCLUDED.algorithm_version, evidence = EXCLUDED.evidence,
            pipeline_run_id = EXCLUDED.pipeline_run_id,
            status = CASE WHEN er.entity_matches.status IN ('accepted', 'rejected')
                          THEN er.entity_matches.status ELSE EXCLUDED.status END,
            updated_at = CASE WHEN er.entity_matches.status IS DISTINCT FROM EXCLUDED.status
                                OR er.entity_matches.match_score IS DISTINCT FROM EXCLUDED.match_score
                              THEN now() ELSE er.entity_matches.updated_at END
    """, {"country": country, "algorithm": ORGANIZATION_LINKING.version, "run": run_id})
    return conn.execute("""
        DELETE FROM er.entity_matches m
        WHERE m.country_code = %s AND m.match_type = 'organization' AND m.status IN ('automatic', 'suggested')
          AND NOT EXISTS (SELECT 1 FROM incoming_org i WHERE i.left_id = m.left_id AND i.right_id = m.right_id)
    """, [country]).rowcount
