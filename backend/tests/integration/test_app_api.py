"""Mobile app API: the `app` schema built from Gold (scripts/app_schema.sql), served by xento_api.

Nothing in a response may reveal where the data came from.
"""

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from xento_api import db as app_db
from xento_api import routes

APP_SCHEMA_SQL = Path(__file__).resolve().parents[3] / "scripts" / "app_schema.sql"

pytestmark = pytest.mark.integration

SOURCE_KEYS = {"sources", "source", "source_count", "source_confidence", "field_sources", "enriched_by", "conflicts",
               "member_count", "wikidata_id", "brand_wikidata", "identifiers", "gold_version_id", "silver_id",
               "source_record_id", "crawl", "crawl_pages", "domain_type", "homepage_status", "link_method"}
SOURCE_VALUES = ("osm", "overture", "geonames", "wikidata", "commoncrawl", "common crawl", "gdelt", "opencorporates",
                 "Q12345", "CC-MAIN")


@pytest.fixture(scope="module")
def client(clean_platform):
    import psycopg

    from data_platform import db

    routes._cache.clear()
    with db.serving() as conn:
        conn.execute("""
            INSERT INTO gold.businesses (gold_id, name, category_group, category, country_code, admin1, city, locality,
                address, latitude, longitude, website, domain, phone, brand_name, brand_wikidata, organization_id,
                sources, source_count, member_count, has_website, has_phone, has_email, has_address, completeness,
                gold_version_id, built_at, field_sources, source_confidence, wikidata_id, enriched_by, conflicts)
            VALUES
              ('gb_1', 'Alpha Pharmacy', 'Health', 'pharmacy', 'PK', 'Punjab', 'Lahore', 'Gulberg', '1 Main Blvd',
               31.52, 74.35, 'alpha.pk', 'alpha.pk', '+924200000', 'Alpha', 'Q12345', 'org_1',
               '{osm,overture}', 2, 2, true, true, false, true, 75, 1, now(), '{"phone": "overture"}', 0.9, 'Q12345',
               '{wikidata}', '{phone}'),
              ('gb_2', 'Beta Bakery', 'Food & Drink', 'bakery', 'PK', 'Punjab', 'Lahore', 'Lahore', NULL,
               31.521, 74.351, NULL, NULL, NULL, NULL, NULL, NULL,
               '{osm}', 1, 1, false, false, false, false, 0, 1, now(), '{}', 0.5, NULL, '{}', '{}'),
              ('gb_3', 'Gamma Garage', 'Automotive', 'unspecified_shop', 'PK', 'Sindh', 'Karachi', 'Clifton', NULL,
               24.86, 67.01, NULL, NULL, '+922100000', NULL, NULL, NULL,
               '{overture}', 1, 1, false, true, false, false, 25, 1, now(), '{}', 0.7, NULL, '{}', '{}'),
              ('gb_4', 'Delta Village', 'Professional Services', 'professional_service', 'PK', 'Sindh', 'Karachi',
               'Saddar', NULL, 24.87, 67.02, 'https://www.wikidata.org/wiki/Q99', 'wikidata.org', NULL, NULL, NULL,
               NULL, '{wikidata}', 1, 1, true, false, false, false, 25, 1, now(), '{}', 0.6, 'Q99', '{}', '{}')
        """)
        conn.execute("""
            INSERT INTO gold.organizations (organization_id, country_code, name, brand_wikidata, domain, category_group,
                location_count, city_count, sources, gold_version_id, wikidata_id, industry, inception, aliases,
                identifiers, field_sources, news_mentions)
            VALUES ('org_1', 'PK', 'Alpha', 'Q12345', 'alpha.pk', 'Health', 1, 1, '{wikidata,gdelt}', 1, 'Q12345',
                    'pharmacy chain', '1999-01-01T00:00:00Z', '{Alpha Pharma}', '{"wikidata": "Q12345"}',
                    '{"industry": "wikidata"}', 4)
        """)
        conn.execute("""
            INSERT INTO gold.cities (city_id, country_code, name, kind, admin1, latitude, longitude, population,
                business_count, with_website, with_phone, organizations, source, gold_version_id)
            VALUES ('geonames:1172451', 'PK', 'Lahore', 'city', 'Punjab', 31.55, 74.35, 13000000, 2, 1, 1, 1,
                    'geonames', 1),
                   ('geonames:1174872', 'PK', 'Karachi', 'city', 'Sindh', 24.86, 67.01, 11600000, 1, 0, 1, 0,
                    'geonames', 1)
        """)
        conn.execute("""
            INSERT INTO gold.domains (domain, country_code, tld, business_count, organization_count, sources,
                gold_version_id, organization_id, domain_type, crawl_pages, homepage_url, homepage_status, crawl)
            VALUES ('alpha.pk', 'PK', 'pk', 1, 1, '{commoncrawl,osm}', 1, 'org_1', 'known_business', 120,
                    'https://alpha.pk/robots.txt', 200, 'CC-MAIN-2026-10'),
                   ('wikidata.org', 'PK', 'org', 1, 0, '{osm}', 1, NULL, NULL, 5, NULL, NULL, NULL)
        """)
    with psycopg.connect(f"dbname={clean_platform}") as conn:      # one transaction, like `make app-schema`
        conn.execute(APP_SCHEMA_SQL.read_text())
    app = FastAPI()
    app.include_router(routes.router)
    yield TestClient(app)
    app_db.close()


def leaks(value, path="$"):
    """Every provenance key or source-identifying value inside a response."""
    found = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in SOURCE_KEYS:
                found.append(f"{path}.{key}")
            found += leaks(item, f"{path}.{key}")
    elif isinstance(value, list):
        for i, item in enumerate(value):
            found += leaks(item, f"{path}[{i}]")
    elif isinstance(value, str) and any(s.lower() in value.lower() for s in SOURCE_VALUES):
        found.append(f"{path}={value!r}")
    return found


def test_every_endpoint_hides_sources(client):
    cities = client.get("/api/app/cities").json()["items"]
    lahore = next(c for c in cities if c["name"] == "Lahore")
    assert lahore["id"].startswith("c") and "1172451" not in lahore["id"]
    urls = ["/api/app/home", "/api/app/businesses", "/api/app/businesses?q=alpha",
            "/api/app/businesses?lat=31.52&lng=74.35&radius_km=2", "/api/app/businesses/gb_1",
            "/api/app/organizations", "/api/app/organizations/org_1", "/api/app/cities", f"/api/app/cities/{lahore['id']}",
            "/api/app/websites", "/api/app/websites/alpha.pk", "/api/app/categories", "/api/app/search?q=a"]
    for url in urls:
        response = client.get(url)
        assert response.status_code == 200, (url, response.text)
        assert leaks(response.json()) == [], (url, json.dumps(response.json())[:500])


def test_app_schema_has_no_provenance(client):
    from data_platform import db

    with db.serving() as conn:
        columns = {r["column_name"] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema = 'app'").fetchall()}
        delta = conn.execute("SELECT website, domain, has_website FROM app.places WHERE id = 'gb_4'").fetchone()
        sites = [r["domain"] for r in conn.execute("SELECT domain FROM app.websites").fetchall()]
    assert not columns & SOURCE_KEYS
    assert delta == {"website": None, "domain": None, "has_website": False}     # provider links are dropped
    assert sites == ["alpha.pk"]


def test_business_detail_is_friendly(client):
    detail = client.get("/api/app/businesses/gb_1").json()
    assert detail["category"] == "Pharmacy" and detail["website"] == "https://alpha.pk"
    assert detail["organization"]["name"] == "Alpha" and detail["city_id"]
    assert [n["id"] for n in detail["nearby"]][0] == "gb_2" and detail["nearby"][0]["distance_km"] < 1
    assert client.get("/api/app/businesses/gb_3").json()["category"] == "Shop"
    assert client.get("/api/app/businesses/nope").status_code == 404


def test_filters_and_nearby(client):
    near = client.get("/api/app/businesses?lat=31.52&lng=74.35&radius_km=5").json()
    assert [b["id"] for b in near["items"]] == ["gb_1", "gb_2"] and near["total"] == 2
    assert client.get("/api/app/businesses?has_phone=true&city=Lahore").json()["total"] == 1
    assert client.get("/api/app/businesses?q=100%25").json()["total"] == 0          # LIKE wildcards are literal
    first = client.get("/api/app/businesses?limit=2").json()
    assert first["next_offset"] == 2 and first["items"][0]["id"] == "gb_1"          # most complete first
    assert client.get("/api/app/businesses?sort=distance").status_code == 422


def test_organization_and_website(client):
    org = client.get("/api/app/organizations/org_1").json()
    assert org["founded"] == "1999" and org["also_known_as"] == ["Alpha Pharma"]
    assert org["top_cities"][0]["name"] == "Lahore" and org["top_cities"][0]["id"]
    assert client.get("/api/app/organizations?q=alpha%20pharma").json()["total"] == 1   # matches aliases
    site = client.get("/api/app/websites/alpha.pk").json()
    assert site["url"] == "https://alpha.pk/" and site["type_label"] == "Business"
    assert client.get("/api/app/websites?type=business").json()["total"] == 1


def test_search_groups_results(client):
    result = client.get("/api/app/search?q=pharm").json()
    assert result["categories"][0]["label"] == "Pharmacy"
    assert result["businesses"][0]["name"] == "Alpha Pharmacy"
    assert result["organizations"][0]["name"] == "Alpha"
