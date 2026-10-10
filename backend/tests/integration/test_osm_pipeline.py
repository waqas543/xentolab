"""End-to-end: OSM extract -> Bronze -> Silver -> entity resolution -> Gold -> serving, plus idempotency."""

import os

import pytest

from data_platform import db, repository
from data_platform.engine.service import start_run
from data_platform.lake.reader import dataset_files, duck, parquet_source

from .conftest import run_to_completion, stage_map

pytestmark = pytest.mark.integration


def start(osm_files, variant=1, **params):
    return start_run("osm.extract", {"country": "PK", "source_path": str(osm_files[variant]), **params},
                     trigger_type="manual", triggered_by="test")


def silver_rows(where: str = "true"):
    con = duck()
    try:
        files = dataset_files("silver.places", {"source": "osm", "country": "PK"})
        rows = con.execute(f"SELECT * FROM {parquet_source(files)} WHERE {where} ORDER BY silver_id").arrow().read_all()
        return {r["source_record_id"]: r for r in rows.to_pylist()}
    finally:
        con.close()


def serving(sql, params=None):
    with db.serving() as conn:
        return conn.execute(sql, params).fetchall()


@pytest.fixture(scope="module")
def first_run(clean_platform, osm_files):
    run = start(osm_files)
    return run_to_completion(run["id"])


def test_first_run_completes_every_stage(first_run):
    assert first_run["status"] == "COMPLETED", first_run["error_summary"]
    stages = stage_map(first_run["id"])
    assert all(s["status"] == "SUCCEEDED" for s in stages.values()), {k: (s["status"], s["error_message"])
                                                                       for k, s in stages.items()}
    assert first_run["bronze_records"] == 42           # 32 nodes + 6 ways + 4 relations
    assert first_run["gold_records"] > 0
    assert first_run["versions"]["parser"] == "osm-bronze-1"


def test_artifact_is_preserved_and_hard_linked(first_run, osm_files):
    artifact = repository.get_artifact(first_run["outputs"]["artifact_id"])
    assert artifact["status"] == "available" and len(artifact["sha256"]) == 64
    assert artifact["source_version"] == "2026-10-01T00:00:00Z"
    from data_platform.storage import get_storage

    stored = get_storage().local_path(artifact["storage_key"])
    assert os.stat(stored).st_ino == os.stat(osm_files[1]).st_ino
    assert repository.get_checkpoint("osm:PK", "replication")["sequence"] == 4900


def test_bronze_preserves_everything(first_run):
    con = duck()
    try:
        artifact_partition = {"artifact_id": first_run["outputs"]["artifact_id"]}
        ways = con.execute(f"SELECT id, node_refs FROM {parquet_source(dataset_files('bronze.osm_ways', artifact_partition))} "
                           "WHERE id = 202").fetchone()
        relation = con.execute(f"SELECT members FROM {parquet_source(dataset_files('bronze.osm_relations', artifact_partition))} "
                               "WHERE id = 301").fetchone()
        lineage = con.execute(f"SELECT DISTINCT source, artifact_id, pipeline_run_id FROM "
                              f"{parquet_source(dataset_files('bronze.osm_nodes', artifact_partition))}").fetchall()
    finally:
        con.close()
    assert ways[1] == [9991, 9992, 9993]
    assert relation[0] == [{"type": "w", "ref": 203, "role": "outer"}]
    assert lineage == [("osm", first_run["outputs"]["artifact_id"], first_run["id"])]


def test_reads_have_no_phantom_partition_columns(first_run):
    # Version directories look like hive partitions (country=PK/source=osm/v=7); they must not become columns.
    con = duck()
    try:
        columns = [r[0] for r in con.execute(
            f"DESCRIBE SELECT * FROM {parquet_source(dataset_files('silver.places', {'source': 'osm'}))}").fetchall()]
    finally:
        con.close()
    assert "v" not in columns and "country" not in columns and columns.count("source") == 1


def test_silver_normalizes_and_enriches(first_run):
    places = silver_rows()
    assert "n105" not in places and "n106" not in places                   # vacant / place of worship excluded
    assert {"n101", "w201", "r301", "n104"} <= set(places)

    systems = places["n107"]
    assert systems["normalized_domain"] == "systems-ltd.com" and systems["normalized_phone"] == "+9242111945227"
    assert systems["name_key"] == "systems" and systems["category_group"] == "Professional Services"
    assert (systems["country_code"], systems["admin1"], systems["admin2"], systems["city"]) == \
           ("PK", "Punjab", "Lahore District", "Lahore")

    assert places["n108"]["domain_is_platform"]
    assert not places["n104"]["has_name"]
    assert places["n110"]["locality"] == "Kasur"
    assert abs(places["w201"]["latitude"] - 32.00115) < 1e-4              # way centroid from Bronze nodes
    assert places["n109"]["country_code"] is None                         # outside every boundary

    con = duck()
    try:
        rejects = con.execute(f"SELECT source_record_id, reason FROM "
                              f"{parquet_source(dataset_files('silver.rejects', {'source': 'osm', 'country': 'PK'}))}").fetchall()
    finally:
        con.close()
    assert rejects == [("w202", "missing_coordinates")]


def test_entity_resolution_links_node_and_building(first_run):
    matches = serving("SELECT left_id, right_id, status FROM er.entity_matches WHERE country_code = 'PK'")
    assert ("osm:n101", "osm:w201", "automatic") in [(m["left_id"], m["right_id"], m["status"]) for m in matches]


def test_gold_merges_duplicates_and_keeps_provenance(first_run):
    aylanto = serving("SELECT * FROM gold.businesses WHERE name = 'Cafe Aylanto'")
    assert len(aylanto) == 1
    business = aylanto[0]
    assert business["member_count"] == 2 and business["sources"] == ["osm"]
    assert business["domain"] == "aylanto.com" and business["phone"] == "+924235781769"

    provenance = serving("SELECT silver_id, artifact_id, is_primary FROM gold.business_sources WHERE gold_id = %s "
                         "ORDER BY silver_id", [business["gold_id"]])
    assert [p["silver_id"] for p in provenance] == ["osm:n101", "osm:w201"]
    assert sum(p["is_primary"] for p in provenance) == 1
    assert all(p["artifact_id"] == first_run["outputs"]["artifact_id"] for p in provenance)

    assert not serving("SELECT 1 FROM gold.businesses WHERE name IS NULL")      # unnamed records are not Gold
    org = serving("SELECT * FROM gold.organizations WHERE brand_wikidata = 'Q1'")
    assert org[0]["location_count"] == 2
    domains = {d["domain"] for d in serving("SELECT domain FROM gold.domains WHERE country_code = 'PK'")}
    assert domains == {"aylanto.com", "systems-ltd.com"}                       # facebook.com is a platform
    khaadi = serving("SELECT website, domain FROM gold.businesses WHERE name = 'Khaadi'")[0]
    assert khaadi["domain"] is None and "facebook.com" in khaadi["website"]


def test_quality_results_recorded(first_run):
    results = repository.get_quality_results(first_run["id"])
    stages = {r["stage"] for r in results}
    assert stages == {"BRONZE_QUALITY", "SILVER_QUALITY", "GOLD_QUALITY"}
    assert not [r for r in results if r["severity"] == "error" and r["status"] != "passed"]


def test_rerun_is_idempotent(first_run, osm_files):
    versions_before = len(repository.list_versions("silver.places"))
    gold_before = serving("SELECT gold_id, gold_version_id FROM gold.businesses ORDER BY gold_id")
    rerun = run_to_completion(start(osm_files)["id"])
    assert rerun["status"] == "COMPLETED"
    stages = stage_map(rerun["id"])
    for stage in ["DOWNLOAD", "BRONZE_INGEST", "BRONZE_QUALITY", "SILVER_TRANSFORM", "SILVER_QUALITY",
                  "ENTITY_RESOLUTION", "GOLD_BUILD", "GOLD_QUALITY", "ANALYTICS_REFRESH"]:
        assert stages[stage]["status"] == "SKIPPED", (stage, stages[stage])
    assert rerun["outputs"]["artifact_id"] == first_run["outputs"]["artifact_id"]
    assert len(repository.list_versions("silver.places")) == versions_before
    assert serving("SELECT gold_id, gold_version_id FROM gold.businesses ORDER BY gold_id") == gold_before


def test_new_artifact_produces_new_versions(first_run, osm_files):
    old_gold = repository.current_versions("gold.businesses", {"country": "PK"})[0]
    run = run_to_completion(start(osm_files, variant=2)["id"])
    assert run["status"] == "COMPLETED"
    assert run["outputs"]["artifact_id"] != first_run["outputs"]["artifact_id"]
    new_gold = repository.current_versions("gold.businesses", {"country": "PK"})[0]
    assert new_gold["id"] != old_gold["id"]
    assert repository.get_version(old_gold["id"])["is_current"] is False          # kept, not deleted
    names = {b["name"] for b in serving("SELECT name FROM gold.businesses")}
    assert "New Cafe" in names and "Khaadi" not in names
    assert serving("SELECT phone FROM gold.businesses WHERE name = 'Systems (Pvt.) Limited'")[0]["phone"] == "+9242111000111"
    # Gold ids are stable for unchanged businesses.
    assert serving("SELECT count(*) AS n FROM gold.businesses WHERE name = 'Cafe Aylanto'")[0]["n"] == 1


def test_backfill_rebuilds_silver_from_stored_artifact(first_run, osm_files):
    artifact_id = first_run["outputs"]["artifact_id"]
    run = run_to_completion(start_run("osm.extract", {"country": "PK", "artifact_id": artifact_id,
                                                      "start_stage": "SILVER_TRANSFORM"},
                                      trigger_type="manual", triggered_by="test")["id"])
    assert run["status"] == "COMPLETED" and run["trigger_type"] == "backfill"
    stages = stage_map(run["id"])
    assert stages["DOWNLOAD"]["status"] == "SKIPPED" and "Backfill" in stages["DOWNLOAD"]["skip_reason"]
    assert stages["SILVER_TRANSFORM"]["status"] == "SUCCEEDED"
    names = {b["name"] for b in serving("SELECT name FROM gold.businesses")}
    assert "Khaadi" in names and "New Cafe" not in names                     # back to the v1 snapshot


def test_rejecting_a_match_splits_the_gold_business(first_run, osm_files):
    with db.serving() as conn:
        conn.execute("""
            UPDATE er.entity_matches SET status = 'rejected', decided_by = 'test', decided_at = now(), updated_at = now()
            WHERE left_id = 'osm:n101' AND right_id = 'osm:w201'
        """)
    run = run_to_completion(start_run("osm.extract", {"country": "PK", "start_stage": "ENTITY_RESOLUTION",
                                                      "rebuild_gold": True},
                                      trigger_type="manual", triggered_by="test")["id"])
    assert run["status"] == "COMPLETED"
    assert serving("SELECT count(*) AS n FROM gold.businesses WHERE name = 'Cafe Aylanto'")[0]["n"] == 2
    # The human decision survived recomputation.
    assert serving("SELECT status FROM er.entity_matches WHERE left_id = 'osm:n101' AND right_id = 'osm:w201'"
                   )[0]["status"] == "rejected"
