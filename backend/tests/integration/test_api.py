"""HTTP APIs end to end (FastAPI TestClient) on the fixture extract: runs, explorer, dashboards, auth."""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from data_platform.api import routes_dashboard, routes_data, routes_pipelines

from .conftest import run_to_completion

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def client(clean_platform):
    app = FastAPI()
    for module in (routes_pipelines, routes_data, routes_dashboard):
        app.include_router(module.router)
    return TestClient(app)


@pytest.fixture(scope="module")
def completed_run(client, osm_files):
    response = client.post("/api/data-platform/pipelines/osm.extract/runs",
                           json={"parameters": {"country": "PK", "source_path": str(osm_files[1]), "rebuild_gold": True}})
    assert response.status_code == 202, response.text
    run = response.json()
    assert run["status"] == "QUEUED"                     # returns immediately; a worker executes it
    return run_to_completion(run["id"])


def test_run_lifecycle_over_http(client, completed_run):
    assert completed_run["status"] == "COMPLETED"
    detail = client.get(f"/api/data-platform/runs/{completed_run['id']}").json()
    assert [s["stage"] for s in detail["stages"]][0] == "DISCOVER"
    assert all(s["progress_percent"] is None or 0 <= s["progress_percent"] <= 100 for s in detail["stages"])
    lineage = client.get(f"/api/data-platform/runs/{completed_run['id']}/lineage").json()
    kinds = {n["kind"] for n in lineage["nodes"]}
    assert {"artifact", "bronze", "silver", "gold"} <= kinds
    assert client.post(f"/api/data-platform/runs/{completed_run['id']}/cancel").status_code == 409
    assert client.post(f"/api/data-platform/runs/{completed_run['id']}/retry").status_code == 409


def test_invalid_parameters_are_rejected(client):
    response = client.post("/api/data-platform/pipelines/osm.extract/runs", json={"parameters": {"country": "Pakistan"}})
    assert response.status_code == 422
    response = client.post("/api/data-platform/pipelines/osm.extract/runs",
                           json={"parameters": {"country": "PK", "api_key": "secret"}})
    assert response.status_code == 422 and "environment" in response.json()["detail"]


def test_cancel_queued_run_over_http(client, osm_files):
    run = client.post("/api/data-platform/pipelines/osm.extract/runs",
                      json={"parameters": {"country": "ZZ", "source_path": str(osm_files[1])}}).json()
    cancelled = client.post(f"/api/data-platform/runs/{run['id']}/cancel").json()
    assert cancelled["status"] == "CANCELLED"
    retried = client.post(f"/api/data-platform/runs/{run['id']}/retry")
    assert retried.status_code == 202 and retried.json()["retry_of_run_id"] == run["id"]
    client.post(f"/api/data-platform/runs/{retried.json()['id']}/cancel")


def test_events_stream_ends_for_finished_runs(client, completed_run):
    with client.stream("GET", f"/api/data-platform/runs/{completed_run['id']}/events") as response:
        body = "".join(response.iter_text())
    assert response.headers["content-type"].startswith("text/event-stream")
    snapshot = json.loads(body.split("data: ", 1)[1].split("\n\n", 1)[0])
    assert snapshot["run"]["status"] == "COMPLETED" and "event: end" in body


def test_explorer_query_with_filters_sort_and_columns(client, completed_run):
    response = client.post("/api/data-platform/datasets/gold.businesses/query", json={
        "filters": {"operator": "and", "conditions": [
            {"field": "city", "operator": "equals", "value": "Lahore"},
            {"field": "has_website", "operator": "equals", "value": True}]},
        "columns": ["name", "domain"], "sort": [{"field": "name", "direction": "desc"}], "page_size": 10})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["columns"] == ["gold_id", "name", "domain"]
    assert [r[1] for r in body["rows"]] == ["Systems (Pvt.) Limited", "Khaadi", "Cafe Aylanto"]


def test_explorer_on_parquet_layers(client, completed_run):
    silver = client.post("/api/data-platform/datasets/silver.places/query",
                         json={"filters": {"operator": "and", "conditions": [
                             {"field": "source", "operator": "in", "value": ["osm"]},
                             {"field": "location", "operator": "within_radius", "value": {"lat": 32.0, "lon": 74.0, "km": 1}}]}})
    assert silver.status_code == 200 and silver.json()["total"] == 2          # restaurant node + building way
    bronze = client.post("/api/data-platform/datasets/bronze.osm_nodes/query",
                         json={"filters": {"operator": "and", "conditions": [{"field": "tags", "operator": "has_key", "value": "shop"}]}})
    assert bronze.status_code == 200 and bronze.json()["total"] >= 4
    values = client.post("/api/data-platform/datasets/gold.businesses/values/category_group", json={"limit": 5}).json()
    assert values[0]["count"] >= 1


def test_explorer_rejects_unsafe_input(client):
    for body in [{"filters": {"operator": "and", "conditions": [{"field": "1; DROP TABLE x", "operator": "equals", "value": 1}]}},
                 {"sort": [{"field": "pg_sleep(10)", "direction": "asc"}]},
                 {"columns": ["name", "tags; --"]}]:
        assert client.post("/api/data-platform/datasets/gold.businesses/query", json=body).status_code == 422


def test_export_streams_filtered_csv(client, completed_run):
    response = client.post("/api/data-platform/datasets/gold.domains/export", json={"columns": ["domain", "business_count"]})
    lines = response.text.strip().splitlines()
    assert lines[0] == "domain,domain,business_count" or lines[0].startswith("domain")
    assert any("aylanto.com" in line for line in lines)


def test_record_provenance_and_match_review(client, completed_run):
    rows = client.post("/api/data-platform/datasets/gold.businesses/query", json={
        "filters": {"operator": "and", "conditions": [{"field": "source_count", "operator": "gte", "value": 1},
                                                      {"field": "name", "operator": "equals", "value": "HBL Bank"}]}}).json()["rows"]
    record = client.get(f"/api/data-platform/datasets/gold.businesses/records/{rows[0][0]}").json()
    source = record["provenance"]["sources"][0]
    assert source["source"] == "osm" and source["artifact"]["sha256"] and source["silver"]["attributes"]["brand:wikidata"] == "Q1"
    matches = client.post("/api/data-platform/datasets/er.entity_matches/query", json={"page_size": 1}).json()
    if matches["rows"]:
        match_id = matches["rows"][0][0]
        decided = client.post(f"/api/data-platform/matches/{match_id}/decision", json={"status": "accepted"}).json()
        assert decided["status"] == "accepted" and decided["decided_by"] == "local-dev"


def test_saved_views(client, completed_run):
    view = client.post("/api/data-platform/views", json={
        "name": "Lahore without websites", "dataset_id": "gold.businesses",
        "query": {"filters": {"operator": "and", "conditions": [{"field": "city", "operator": "equals", "value": "Lahore"},
                                                                {"field": "has_website", "operator": "equals", "value": False}]}}})
    assert view.status_code == 201
    assert any(v["name"] == "Lahore without websites" for v in client.get("/api/data-platform/views?dataset_id=gold.businesses").json())
    assert client.delete(f"/api/data-platform/views/{view.json()['id']}").json() == {"ok": True}


def test_dashboards_share_the_filter_dsl(client, completed_run):
    lahore = json.dumps({"operator": "and", "conditions": [{"field": "city", "operator": "in", "value": ["Lahore"]}]})
    all_ = client.get("/api/data-platform/dashboard/overview").json()["kpis"]
    filtered = client.get("/api/data-platform/dashboard/overview", params={"filters": lahore}).json()["kpis"]
    assert 0 < filtered["businesses"] < all_["businesses"]
    assert all_["raw_records"] == 42 and all_["silver_records"] > 0
    for endpoint in ["sources", "geography", "categories", "domains", "quality", "pipelines"]:
        response = client.get(f"/api/data-platform/dashboard/{endpoint}", params={"filters": lahore} if endpoint != "pipelines" else None)
        assert response.status_code == 200, (endpoint, response.text)
    domains = client.get("/api/data-platform/dashboard/domains").json()
    assert domains["summary"]["platform_only"] >= 1                               # Khaadi's facebook page
    assert client.get("/api/data-platform/dashboard/overview", params={"filters": "{bad json"}).status_code == 422


def test_prometheus_metrics(client, completed_run):
    text = client.get("/api/data-platform/metrics").text
    assert 'pipeline_runs_total{source="osm",status="COMPLETED"}' in text and "stage_duration_seconds_sum" in text


def test_api_key_mode_enforces_permissions(client, monkeypatch):
    from data_platform.api.auth import create_api_key
    from data_platform.config import get_settings

    create_api_key("viewer", "dpk_viewer_key", ["data_platform.view"])
    monkeypatch.setenv("DATA_PLATFORM_AUTH_MODE", "api_key")
    get_settings.cache_clear()
    try:
        assert client.get("/api/data-platform/runs").status_code == 401
        headers = {"x-api-key": "dpk_viewer_key"}
        assert client.get("/api/data-platform/runs", headers=headers).status_code == 200
        assert client.post("/api/data-platform/pipelines/osm.extract/runs", json={"parameters": {"country": "PK"}},
                           headers=headers).status_code == 403
        bronze = client.post("/api/data-platform/datasets/bronze.osm_nodes/query", json={}, headers=headers)
        assert bronze.status_code == 403                                         # raw data needs view_raw_data
        assert client.get("/api/data-platform/runs", headers={"x-api-key": "wrong"}).status_code == 401
    finally:
        monkeypatch.setenv("DATA_PLATFORM_AUTH_MODE", "dev")
        get_settings.cache_clear()
