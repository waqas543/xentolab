"""Overture adapter end to end, and cross-source resolution with OSM (local fixture release, no network)."""

import json
import os
import threading
import time

import pytest

from data_platform import db, repository
from data_platform.engine.service import start_run
from data_platform.lake.reader import dataset_files, duck, parquet_source

from .conftest import run_to_completion, stage_map

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def overture_release(tmp_path_factory):
    from tests.fixtures.overture_fixture import write_release

    url = write_release(tmp_path_factory.mktemp("overture"))
    previous = os.environ.get("OVERTURE_STAC_URL")
    os.environ["OVERTURE_STAC_URL"] = url
    yield url
    if previous is None:
        os.environ.pop("OVERTURE_STAC_URL", None)
    else:
        os.environ["OVERTURE_STAC_URL"] = previous


@pytest.fixture(scope="module")
def runs(clean_platform, osm_files, overture_release):
    osm = run_to_completion(start_run("osm.extract", {"country": "PK", "source_path": str(osm_files[1])},
                                      trigger_type="manual", triggered_by="test")["id"])
    overture = run_to_completion(start_run("overture.places", {"country": "PK"}, trigger_type="manual",
                                           triggered_by="test")["id"])
    return {"osm": osm, "overture": overture}


def serving(sql, params=None):
    with db.serving() as conn:
        return conn.execute(sql, params).fetchall()


def query(dataset, partition, sql_tail="", columns="*"):
    con = duck()
    try:
        files = dataset_files(dataset, partition)
        table = con.execute(f"SELECT {columns} FROM {parquet_source(files)} {sql_tail}").arrow().read_all()
        return table.to_pylist()
    finally:
        con.close()


def test_overture_run_completes(runs):
    run = runs["overture"]
    assert runs["osm"]["status"] == "COMPLETED"
    assert run["status"] == "COMPLETED", run["error_summary"]
    stages = stage_map(run["id"])
    assert all(s["status"] == "SUCCEEDED" for s in stages.values()), {k: (s["status"], s["error_message"]) for k, s in stages.items()}


def test_download_reads_only_the_country(runs):
    artifact = repository.get_artifact(runs["overture"]["outputs"]["artifact_id"])
    assert artifact["source_version"] == "2026-01-01.0" and artifact["format"] == "geoparquet"
    assert artifact["source_checksum"] == "overture:2026-01-01.0:places:PK"
    assert [f["id"] for f in artifact["metadata"]["files"]] == ["00000"]          # far-away file never opened
    assert artifact["metadata"]["row_groups"] == 1                               # far-away row group pruned
    assert artifact["metadata"]["rows"] == 8                                     # Indian record filtered out
    download = stage_map(runs["overture"]["id"])["DOWNLOAD"]
    assert download["progress_total"] == 1 and download["progress_current"] == 1  # real percentage: row groups


def test_bronze_preserves_provider_columns(runs):
    rows = query("bronze.overture_places", {"artifact_id": runs["overture"]["outputs"]["artifact_id"]},
                 "ORDER BY id", "id, sources, taxonomy, source, artifact_id")
    assert len(rows) == 8
    assert rows[0]["sources"][0]["dataset"] == "meta" and rows[0]["source"] == "overture"


def test_silver_overture_contract(runs):
    places = {r["source_record_id"]: r for r in query("silver.places", {"source": "overture", "country": "PK"})}
    assert "ov-mosque" not in places and "ov-india" not in places          # not a business / other country
    assert set(places) == {"ov-aylanto", "ov-systems", "ov-fazaldin", "ov-bookshop", "ov-kiosk", "ov-unknown"}
    pharmacy = places["ov-fazaldin"]
    assert (pharmacy["category_group"], pharmacy["normalized_domain"]) == ("Health", "fazaldin.com")
    # Geography comes from the shared Silver datasets that the OSM run produced.
    assert (pharmacy["admin1"], pharmacy["admin2"], pharmacy["locality"]) == ("Punjab", "Lahore District", "Kasur")
    assert places["ov-aylanto"]["social_links"] == ["https://www.facebook.com/aylanto"]
    assert places["ov-aylanto"]["normalized_phone"] == "+924235781769"
    assert places["ov-kiosk"]["source_confidence"] == pytest.approx(0.12)
    assert places["ov-unknown"]["category"] is None and places["ov-unknown"]["category_group"] == "Other Services"
    assert places["ov-systems"]["silver_id"] == "overture:ov-systems"
    attributes = json.loads(places["ov-aylanto"]["attributes"])
    assert attributes["sources"][0]["record_id"] == "fb-ov-aylanto"          # raw provider payload retained
    rejects = query("silver.rejects", {"source": "overture", "country": "PK"})
    assert [(r["source_record_id"], r["reason"]) for r in rejects] == [("ov-closed", "permanently_closed")]


def test_cross_source_matches_and_gold(runs):
    matches = {(m["left_id"], m["right_id"]): m["status"]
               for m in serving("SELECT left_id, right_id, status FROM er.entity_matches")}
    assert matches[("osm:n101", "overture:ov-aylanto")] == "automatic"
    assert matches[("osm:n107", "overture:ov-systems")] == "automatic"

    aylanto = serving("SELECT * FROM gold.businesses WHERE name = 'Cafe Aylanto'")
    assert len(aylanto) == 1
    assert aylanto[0]["sources"] == ["osm", "overture"] and aylanto[0]["member_count"] == 3
    assert aylanto[0]["field_sources"]["name"] == "osm"                       # OSM ranks first for names/positions

    pharmacy = serving("SELECT * FROM gold.businesses WHERE name = 'Fazal Din Pharma'")[0]
    assert pharmacy["domain"] == "fazaldin.com"
    assert pharmacy["field_sources"]["website"] == "overture"                 # enriched by Overture
    assert pharmacy["field_sources"]["phone"] == "osm"

    names = {b["name"] for b in serving("SELECT name FROM gold.businesses")}
    assert "Readings Bookshop" in names                                       # Overture-only business
    assert "Mystery Kiosk" not in names                                       # below the Gold confidence floor
    gold = runs["overture"]["outputs"]["gold_versions"]["gold.businesses"]
    from data_platform.gold.version import GOLD_VERSION

    assert repository.get_version(gold)["versions"]["gold"] == GOLD_VERSION


def test_adding_a_source_does_not_trip_row_count_stability(runs):
    # Gold for PK was first built from OSM only (in the OSM run), then from OSM + Overture.
    result = [r for r in repository.get_quality_results(runs["overture"]["id"])
              if r["dataset_id"] == "gold.businesses" and r["check_name"] == "row_count_stable"][0]
    assert result["status"] == "passed" and "inputs changed" in result["details"]["note"]


def test_failed_quality_gate_is_reevaluated_on_retry(runs):
    from data_platform.quality.runner import already_checked

    gold = runs["overture"]["outputs"]["gold_versions"]["gold.businesses"]
    assert already_checked(gold, "GOLD_QUALITY")
    repository.record_quality_results([{
        "pipeline_run_id": runs["overture"]["id"], "stage_run_id": None, "stage": "GOLD_QUALITY",
        "dataset_id": "gold.businesses", "dataset_version_id": gold, "check_name": "synthetic", "check_type": "custom",
        "severity": "error", "status": "failed", "records_checked": 1, "records_failed": 1,
        "failure_percentage": 100.0, "threshold_percentage": 0.0, "details": {}}])
    assert not already_checked(gold, "GOLD_QUALITY")


def test_rerun_is_idempotent(runs):
    rerun = run_to_completion(start_run("overture.places", {"country": "PK"}, trigger_type="manual",
                                        triggered_by="test")["id"])
    stages = stage_map(rerun["id"])
    assert stages["DOWNLOAD"]["status"] == "SKIPPED" and "already stored" in stages["DOWNLOAD"]["skip_reason"]
    for stage in ["BRONZE_INGEST", "SILVER_TRANSFORM", "ENTITY_RESOLUTION", "GOLD_BUILD", "ANALYTICS_REFRESH"]:
        assert stages[stage]["status"] == "SKIPPED", stage


def test_overlap_dashboard(runs):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from data_platform.api import routes_dashboard

    app = FastAPI()
    app.include_router(routes_dashboard.router)
    body = TestClient(app).get("/api/data-platform/dashboard/overlap").json()
    combos = {tuple(c["sources"]): c["businesses"] for c in body["combinations"]}
    assert combos[("osm", "overture")] >= 3 and combos[("overture",)] >= 1 and combos[("osm",)] >= 1
    enriched = {(e["field"], e["by"]): e["businesses"] for e in body["enrichment"]}
    assert enriched[("website", "overture")] >= 1                             # the pharmacy


def test_gold_scope_lock_serializes_and_stays_cancellable(runs):
    from data_platform.engine.locks import gold_scope_lock
    from data_platform.errors import RunCancelled

    class Ctx:
        params = {"country": "LOCKTEST"}
        cancelled = False

        def progress(self, *args, **kwargs):
            if self.cancelled:
                raise RunCancelled()

    holder_inside, release = threading.Event(), threading.Event()

    def hold():
        with gold_scope_lock(Ctx()):
            holder_inside.set()
            release.wait(10)

    thread = threading.Thread(target=hold)
    thread.start()
    assert holder_inside.wait(5)
    waiter = Ctx()
    timer = threading.Timer(1.5, lambda: setattr(waiter, "cancelled", True))
    timer.start()
    started = time.monotonic()
    with pytest.raises(RunCancelled):
        with gold_scope_lock(waiter, poll_seconds=0.2):
            pass                                                              # must not get here while held
    assert time.monotonic() - started >= 1.4
    release.set()
    thread.join(5)
    with gold_scope_lock(Ctx(), poll_seconds=0.2):                            # free again
        pass
