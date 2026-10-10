"""GeoNames, Wikidata, Common Crawl, GDELT and OpenCorporates end to end, and how they enrich Gold.

All providers are served by a local fixture server (no network). Order matters and mirrors
real use: OSM first (locations), then the reference / enrichment sources.
"""

import json
import os

import pytest

from data_platform import db, repository
from data_platform.engine.service import start_run
from data_platform.lake.reader import dataset_files, duck, parquet_source

from .conftest import run_to_completion, stage_map

pytestmark = pytest.mark.integration

ENV = {"GEONAMES_BASE_URL": "/geonames", "WIKIDATA_SPARQL_URL": "/wikidata/sparql",
       "WIKIDATA_API_URL": "/wikidata/api.php", "COMMONCRAWL_DATA_URL": "/cc", "GDELT_BASE_URL": "/gdelt",
       "OPENCORPORATES_API_URL": "/oc"}


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    from tests.fixtures import sources_fixture as fx
    from tests.fixtures.http_server import FixtureServer

    root = tmp_path_factory.mktemp("providers")
    fx.write_all(root)
    with FixtureServer(root) as srv:
        srv.routes["/wikidata/sparql"] = fx.sparql_route
        srv.routes["/wikidata/api.php"] = fx.wbgetentities_route
        srv.opencorporates = fx.OpenCorporatesAPI(quota=2)
        srv.routes["/oc/companies/search"] = srv.opencorporates
        previous = {k: os.environ.get(k) for k in ENV}
        os.environ.update({k: srv.url + v for k, v in ENV.items()})
        yield srv
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def run(pipeline: str, **params) -> dict:
    return run_to_completion(start_run(pipeline, {"country": "PK", **params}, trigger_type="manual",
                                       triggered_by="test")["id"])


@pytest.fixture(scope="module")
def runs(clean_platform, osm_files, server):
    from data_platform.config import get_settings

    result = {"osm": run("osm.extract", source_path=str(osm_files[1])),
              "geonames": run("geonames.country"),
              "wikidata": run("wikidata.organizations"),
              "commoncrawl": run("commoncrawl.domains"),
              "gdelt": run("gdelt.mentions", hours=1)}
    result["opencorporates_without_token"] = run("opencorporates.lookup")
    os.environ["DP_SECRET_OPENCORPORATES_API_TOKEN"] = "test-token-123"
    get_settings.cache_clear()
    try:
        result["opencorporates"] = run("opencorporates.lookup")
    finally:
        os.environ.pop("DP_SECRET_OPENCORPORATES_API_TOKEN", None)
        get_settings.cache_clear()
    return result


def serving(sql, params=None):
    with db.serving() as conn:
        return conn.execute(sql, params).fetchall()


def query(dataset, partition, tail="", columns="*"):
    con = duck()
    try:
        return con.execute(f"SELECT {columns} FROM {parquet_source(dataset_files(dataset, partition))} {tail}") \
            .arrow().read_all().to_pylist()
    finally:
        con.close()


def assert_completed(run_row):
    stages = stage_map(run_row["id"])
    assert run_row["status"] == "COMPLETED", (run_row["error_summary"],
                                              {k: (s["status"], s["error_message"]) for k, s in stages.items()})


@pytest.mark.parametrize("name", ["osm", "geonames", "wikidata", "commoncrawl", "gdelt", "opencorporates"])
def test_runs_complete(runs, name):
    assert_completed(runs[name])


# ---------------------------------------------------------------- GeoNames

def test_geonames_gazetteer(runs):
    places = {p["name"]: p for p in query("silver.geo_places", {"source": "geonames", "country": "PK"})}
    assert set(places) == {"Lahore", "Kasur", "Testabad", "Model Town"}           # abandoned town excluded
    assert (places["Lahore"]["kind"], places["Kasur"]["kind"], places["Testabad"]["kind"],
            places["Model Town"]["kind"]) == ("city", "city", "village", "suburb")
    assert places["Lahore"]["name_local"] == "لاہور" and places["Lahore"]["population"] == 11126285
    assert (places["Lahore"]["admin1"], places["Lahore"]["admin2"]) == ("Punjab", "Lahore District")
    postal = query("silver.postal_codes", {"source": "geonames", "country": "PK"})
    assert [(p["postal_code"], p["admin1"]) for p in postal] == [("54000", "Punjab")]


def test_geonames_facilities_join_places(runs):
    facilities = {p["name"]: p for p in query("silver.places", {"source": "geonames", "country": "PK"})}
    assert set(facilities) == {"Pearl Test Hotel", "Test College", "Testabad Sugar Mills"}   # station excluded
    college = facilities["Test College"]
    assert (college["category_group"], college["wikidata"], college["admin1"]) == ("Education", "Q300", "Punjab")
    assert facilities["Testabad Sugar Mills"]["category_group"] == "Industry & Craft"


def test_geography_now_prefers_the_gazetteer(runs):
    from data_platform.silver_contract import geography_versions

    boundaries, places = geography_versions("PK")
    assert [b["partition"]["source"] for b in boundaries] == ["osm"]
    assert [p["partition"]["source"] for p in places] == ["geonames"]
    # OSM Silver was placed with OSM's own towns: a re-run rebuilds it with the shared geography.
    rerun = run("osm.extract", source_path=runs["osm"]["parameters"]["source_path"])
    assert_completed(rerun)
    assert stage_map(rerun["id"])["SILVER_TRANSFORM"]["status"] == "SUCCEEDED"
    pharmacy = query("silver.places", {"source": "osm", "country": "PK"}, "WHERE name = 'Fazal Din Pharma'")[0]
    assert pharmacy["city"] == "Kasur"                                              # GeoNames spelling (ASCII)


# ---------------------------------------------------------------- Wikidata

def test_wikidata_snapshot_and_silver(runs):
    artifact = repository.get_artifact(runs["wikidata"]["outputs"]["artifact_id"])
    assert artifact["metadata"]["items"] == 4 and artifact["metadata"]["fetched"] == 4
    orgs = {o["source_record_id"]: o for o in query("silver.organizations", {"source": "wikidata", "country": "PK"})}
    assert set(orgs) == {"Q1", "Q100", "Q300", "Q400"}                              # Q1: referenced by OSM brands
    systems = orgs["Q100"]
    assert (systems["normalized_domain"], systems["industries"], systems["headquarters"]) == \
        ("systems-ltd.com", ["software industry"], "Lahore")
    assert (systems["inception"], systems["employees"], systems["entity_type"]) == ("1977", 6000, "company")
    assert json.loads(systems["identifiers"])["opencorporates"] == "pk/1234"
    assert systems["social_links"] == ["https://twitter.com/SystemsLtdPK"]
    assert orgs["Q1"]["category_group"] == "Finance" and json.loads(orgs["Q1"]["attributes"])["role"] == "referenced"


def test_wikidata_rerun_reuses_unchanged_entities(runs):
    rerun = run("wikidata.organizations")
    assert_completed(rerun)
    stages = stage_map(rerun["id"])
    assert stages["DOWNLOAD"]["status"] == "SKIPPED" and "No Wikidata edits" in stages["DOWNLOAD"]["skip_reason"]


# ---------------------------------------------------------------- Common Crawl

def test_commoncrawl_scope_and_domains(runs):
    artifact = repository.get_artifact(runs["commoncrawl"]["outputs"]["artifact_id"])
    assert sorted(artifact["metadata"]["known_domains"]) == ["aylanto.com", "systems-ltd.com"]
    captures = query("bronze.commoncrawl_captures", {"artifact_id": artifact["id"]}, columns="urlkey, scope")
    keys = {c["urlkey"]: c["scope"] for c in captures}
    assert "com,example)/" not in keys and "org,unrelated)/" not in keys and "pl,sklep)/" not in keys
    assert keys["pk,com,newshop)/"] == "tld" and keys["com,aylanto)/"] == "known_domain"
    domains = {d["domain"]: d for d in query("silver.web_domains", {"source": "commoncrawl", "country": "PK"})}
    assert set(domains) == {"aylanto.com", "systems-ltd.com", "casino-spam.com.pk", "newshop.com.pk",
                            "testuni.edu.pk", "punjab.gov.pk"}
    shop = domains["newshop.com.pk"]
    assert (shop["pages"], shop["host_count"], shop["domain_type"], shop["top_language"]) == (3, 2, "commercial", "urd")
    assert shop["homepage_url"] == "https://newshop.com.pk/" and shop["is_business_candidate"]
    assert shop["warc_filename"].startswith("crawl-data/") and shop["warc_length"] == 1234
    assert not domains["casino-spam.com.pk"]["is_business_candidate"]
    assert domains["testuni.edu.pk"]["domain_type"] == "education"
    assert domains["punjab.gov.pk"]["homepage_status"] == 503 and domains["aylanto.com"]["tld"] == "com"


# ---------------------------------------------------------------- GDELT

def test_gdelt_keeps_only_the_country(runs):
    artifact = repository.get_artifact(runs["gdelt"]["outputs"]["artifact_id"])
    assert artifact["metadata"]["missing_files"] == 5 and artifact["metadata"]["records"] == 3
    mentions = {m["name_key"]: m for m in query("silver.org_mentions", {"source": "gdelt", "country": "PK"})}
    assert "tata group" not in mentions and "associated press" not in mentions          # other country / noise
    assert "germany" not in mentions                                    # a country (GeoNames list), not an org
    assert mentions["systems"]["mentions"] == 2 and mentions["systems"]["articles"] == 2
    assert mentions["systems"]["name"] == "Systems Limited"
    assert mentions["habib bank"]["top_locations"] == ["Lahore, Punjab, Pakistan", "Pakistan"]
    assert repository.get_checkpoint("gdelt:PK", "last_file") == "20261009120000"


def test_gdelt_continues_from_checkpoint(runs):
    rerun = run("gdelt.mentions", hours=1)
    assert_completed(rerun)
    assert "No new GDELT files" in stage_map(rerun["id"])["DOWNLOAD"]["skip_reason"]


# ---------------------------------------------------------------- OpenCorporates

def test_opencorporates_needs_a_token(runs):
    failed = runs["opencorporates_without_token"]
    assert failed["status"] == "FAILED" and "DP_SECRET_OPENCORPORATES_API_TOKEN" in failed["error_summary"]


def test_opencorporates_respects_quota_and_hides_token(runs, server):
    artifact = repository.get_artifact(runs["opencorporates"]["outputs"]["artifact_id"])
    assert artifact["metadata"]["requests"] == 2 and "429" in artifact["metadata"]["quota_hit"]
    assert server.opencorporates.calls == 3                                         # stopped at the first 429
    records = {o["registry_id"]: o for o in query("silver.organizations", {"source": "opencorporates", "country": "PK"})}
    assert set(records) == {"pk/1234", "pk/5678"}
    assert records["pk/1234"]["name"] == "Systems Limited" and records["pk/1234"]["legal_name"] == "SYSTEMS LIMITED"
    with db.control() as conn:
        leaked = conn.execute("""
            SELECT (SELECT count(*) FROM dp.pipeline_logs WHERE message || coalesce(context::text, '') LIKE '%test-token%')
                 + (SELECT count(*) FROM dp.pipeline_errors
                    WHERE message || coalesce(context::text, '') || coalesce(traceback, '') LIKE '%test-token%')
                 + (SELECT count(*) FROM dp.pipeline_runs WHERE parameters::text || outputs::text LIKE '%test-token%')
                 + (SELECT count(*) FROM dp.dataset_artifacts WHERE metadata::text LIKE '%test-token%') AS n
        """).fetchone()["n"]
    assert leaked == 0


# ---------------------------------------------------------------- entity resolution and Gold

def test_organization_links(runs):
    links = {(m["left_id"], m["right_id"]): (m["status"], m["evidence"]["method"]) for m in serving(
        "SELECT * FROM er.entity_matches WHERE match_type = 'organization'")}
    assert links[("osm:n107", "wikidata:Q100")] == ("automatic", "domain")
    assert links[("osm:n102", "wikidata:Q1")] == ("automatic", "wikidata_id")
    assert links[("geonames:2002", "wikidata:Q300")] == ("automatic", "wikidata_id")
    assert links[("opencorporates:pk/1234", "wikidata:Q100")] == ("automatic", "identifier")
    # Organization links never merge locations: the two HBL branches stay two businesses.
    hbl = serving("SELECT * FROM gold.businesses WHERE brand_wikidata = 'Q1'")
    assert len(hbl) == 2 and len({b["organization_id"] for b in hbl}) == 1


def test_gold_organizations(runs):
    orgs = {o["name"]: o for o in serving("SELECT * FROM gold.organizations")}
    systems = orgs["Systems Limited"]
    assert systems["location_count"] == 1 and systems["wikidata_id"] == "Q100"
    assert systems["sources"] == ["commoncrawl", "gdelt", "opencorporates", "osm", "wikidata"] or \
        systems["sources"] == ["gdelt", "opencorporates", "osm", "wikidata"]
    assert (systems["legal_name"], systems["registry_id"], systems["registry_status"]) == \
        ("SYSTEMS LIMITED", "pk/1234", "Active")
    assert systems["industry"] == "software industry" and systems["news_mentions"] == 2
    assert systems["field_sources"]["registry"] == "opencorporates"
    bank = orgs["Habib Bank Limited"]
    assert (bank["location_count"], bank["wikidata_id"], bank["registry_id"]) == (2, "Q1", "pk/5678")
    assert bank["news_mentions"] == 2
    lonely = orgs["Lonely Organization"]
    assert lonely["location_count"] == 0 and lonely["sources"] == ["wikidata"]       # knowledge-base discovery
    provenance = {r["silver_id"]: r for r in serving(
        "SELECT * FROM gold.organization_sources WHERE organization_id = %s", [systems["organization_id"]])}
    assert set(provenance) == {"wikidata:Q100", "opencorporates:pk/1234"}
    assert provenance["wikidata:Q100"]["link_method"] == "domain"


def test_gold_businesses_record_enrichment(runs):
    systems = serving("SELECT * FROM gold.businesses WHERE name = 'Systems (Pvt.) Limited'")[0]
    assert set(systems["enriched_by"]) >= {"wikidata", "opencorporates", "gdelt", "commoncrawl"}
    college = serving("SELECT * FROM gold.businesses WHERE name = 'Test College'")[0]
    assert college["wikidata_id"] == "Q300" and college["sources"] == ["geonames"]


def test_gold_domains_include_discovered(runs):
    domains = {d["domain"]: d for d in serving("SELECT * FROM gold.domains")}
    assert domains["newshop.com.pk"]["business_count"] == 0 and domains["newshop.com.pk"]["sources"] == ["commoncrawl"]
    assert domains["aylanto.com"]["business_count"] == 1 and domains["aylanto.com"]["crawl_pages"] == 2
    assert "commoncrawl" in domains["aylanto.com"]["sources"]
    assert domains["hbl.com"]["sources"] == ["wikidata"]                            # stated by Wikidata only


def test_gold_cities(runs):
    cities = {c["name"]: c for c in serving("SELECT * FROM gold.cities")}
    lahore = cities["Lahore"]
    assert lahore["population"] == 11126285 and lahore["business_count"] >= 5 and lahore["source"] == "geonames"
    assert lahore["businesses_per_10k"] == round(10000 * lahore["business_count"] / 11126285, 2)
    assert cities["Kasur"]["business_count"] >= 1


def test_overlap_dashboard_reports_enrichment(runs):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from data_platform.api import routes_dashboard

    app = FastAPI()
    app.include_router(routes_dashboard.router)
    body = TestClient(app).get("/api/data-platform/dashboard/overlap").json()
    enrichers = {(e["source"], e["by"]): e["businesses"] for e in body["enriched_by"]}
    assert enrichers[("osm", "wikidata")] >= 3                       # n102, n103 (HBL brand) + n107 (Systems)
    assert enrichers[("osm", "commoncrawl")] >= 2


NEW_DATASETS = ["bronze.geonames_places", "bronze.wikidata_entities", "bronze.commoncrawl_captures", "bronze.gdelt_gkg",
                "bronze.opencorporates_companies", "silver.geo_places", "silver.postal_codes", "silver.organizations",
                "silver.web_domains", "silver.org_mentions", "gold.organizations", "gold.organization_sources",
                "gold.domains", "gold.cities", "gold.businesses", "er.entity_matches"]


@pytest.mark.parametrize("dataset_id", NEW_DATASETS)
def test_explorer_queries_every_new_dataset(runs, dataset_id):
    from data_platform.query import service
    from data_platform.query.dsl import QueryRequest
    from data_platform.query.registry import get_dataset

    spec = get_dataset(dataset_id)
    page = service.run_query(spec, QueryRequest(page=1, page_size=5))
    assert page["rows"], dataset_id
    facet = next((c for c in spec.columns if c.facet), None)
    if facet:
        assert service.facet_values(spec, facet.name, search=None, filters=None, limit=5) is not None


def test_organization_record_shows_provenance(runs):
    from data_platform.api.routes_data import organization_provenance

    org = serving("SELECT organization_id FROM gold.organizations WHERE name = 'Systems Limited'")[0]
    detail = organization_provenance(org["organization_id"], "PK")
    assert {r["source"] for r in detail["records"]} == {"wikidata", "opencorporates"}
    wikidata = next(r for r in detail["records"] if r["source"] == "wikidata")
    assert wikidata["silver"]["identifiers"]["opencorporates"] == "pk/1234"
    assert [loc["name"] for loc in detail["locations"]] == ["Systems (Pvt.) Limited"]


def test_dashboard_endpoints_for_new_sources(runs):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from data_platform.api import routes_dashboard

    app = FastAPI()
    app.include_router(routes_dashboard.router)
    client = TestClient(app)
    orgs = client.get("/api/data-platform/dashboard/organizations").json()
    assert orgs["kpis"]["records_only"] >= 1 and orgs["kpis"]["with_registry"] == 2
    assert orgs["top_news"][0]["news_mentions"] == 2
    assert {r["label"] for r in orgs["by_source"]} >= {"wikidata", "opencorporates", "gdelt", "osm"}
    assert all(m["name_key"] not in ("systems", "habib bank") for m in orgs["unmatched_news"])
    crawl = client.get("/api/data-platform/dashboard/domains").json()["crawl"]
    assert crawl["summary"]["business_domains_crawled"] == 2
    assert [lead["label"] for lead in crawl["discovery_leads"]] == ["newshop.com.pk"]
    city = client.get("/api/data-platform/dashboard/cities").json()
    assert city["largest"][0]["name"] == "Lahore" and city["underserved"]


def test_overview_lists_every_source_and_its_contribution(runs):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from data_platform.api import routes_dashboard

    app = FastAPI()
    app.include_router(routes_dashboard.router)
    body = TestClient(app).get("/api/data-platform/dashboard/overview").json()
    rows = {c["source"]: c for c in body["contributions"]}
    assert set(rows) == {"osm", "overture", "geonames", "wikidata", "commoncrawl", "gdelt", "opencorporates"}
    assert rows["osm"]["businesses_found"] > 0 and rows["osm"]["businesses_enriched"] == 0
    assert rows["wikidata"]["businesses_found"] == 0 and rows["wikidata"]["businesses_enriched"] >= 3
    assert rows["wikidata"]["silver"]["silver.organizations"] == 4
    assert rows["commoncrawl"]["domains"] == 6 and rows["gdelt"]["businesses_enriched"] >= 1
    assert rows["opencorporates"]["missing_credentials"] == ["DP_SECRET_OPENCORPORATES_API_TOKEN"]
