"""Google Maps adapter end to end: re-runs, appended records and duplicates within and across sources."""

import json

import pytest

from data_platform import db, repository
from data_platform.engine.service import start_run
from data_platform.lake.reader import dataset_files, duck, parquet_source

from .conftest import run_to_completion, stage_map

pytestmark = pytest.mark.integration


def listing(place_id, name, lat, lng, *, category, query="restaurants in Lahore Pakistan", **extra):
    return {"result_type": "list", "name": name, "place_id": place_id, "cid": f"cid-{place_id}",
            "feature_id": f"0x1:{place_id}", "lat": lat, "lng": lng, "category": category, "rating": 4.5,
            "reviews": 120, "address": None, "phone": None, "website": None, "domain": None, "hours": None,
            "sponsored": False, "search_city": "Lahore", "search_area": "", "search_province": "Punjab",
            "search_category_group": "Food & drink", "search_category": "restaurants", "search_query": query,
            **extra}


AYLANTO = listing("g-aylanto", "Cafe Aylanto", 32.0011, 74.0011, category="Italian restaurant",
                  phone="+92 42 35781769")                                   # OSM has it: same phone, 14 m away
FAZAL = listing("g-fazal", "Fazal Din Pharma", 31.2010, 74.5011, category="Pharmacy", phone="+92 300 1234567",
                search_category_group="Health", search_category="pharmacies")
BIRYANI = listing("ChIJCbiryani", "Lahore Biryani House", 32.0200, 74.0200, category="Biryani restaurant",
                  phone="+92 42 35111111", address="12 Mall Rd")             # only Google knows it
BIRYANI_AGAIN = {**BIRYANI, "search_query": "biryani in Lahore Pakistan"}   # found again by another search
# Google's own duplicate listing of the same restaurant. Its id sorts differently in bytes than in en_US collation.
TWIN = {**BIRYANI, "place_id": "ChIJ_biryani", "cid": "cid-twin", "feature_id": "0x1:twin", "lat": 32.02003,
        "phone": None}
MOSQUE = listing("g-mosque", "Badshahi Masjid", 32.0250, 74.0250, category="Mosque")
CLOSED = listing("g-closed", "Old Cafe", 32.0260, 74.0260, category="Cafe", hours="Permanently closed")
GLITCH = listing("g-glitch", "Shifa Medical Store", 32.0270, 74.0270, category="Gulberg III, Lahore",
                 search_category_group="Health", search_category="pharmacies")
NO_ID = {"result_type": "place", "name": "Some Police Station", "lat": None, "lng": None,
         "search_city": "Lahore", "search_query": "police stations in Lahore Pakistan"}


def write(path, records, tail=""):
    path.write_text("".join(json.dumps(r) + "\n" for r in records) + tail)


def run(folder) -> dict:
    return run_to_completion(start_run("googlemaps.places", {"country": "PK", "source_path": str(folder)},
                                       trigger_type="manual", triggered_by="test")["id"])


def query(dataset, partition, tail="", columns="*"):
    con = duck()
    try:
        return con.execute(f"SELECT {columns} FROM {parquet_source(dataset_files(dataset, partition))} {tail}") \
            .arrow().read_all().to_pylist()
    finally:
        con.close()


def serving(sql, params=None):
    with db.serving() as conn:
        return conn.execute(sql, params).fetchall()


def assert_completed(run_row):
    stages = stage_map(run_row["id"])
    assert run_row["status"] == "COMPLETED", (run_row["error_summary"],
                                              {k: (s["status"], s["error_message"]) for k, s in stages.items()})


@pytest.fixture(scope="module")
def folder(tmp_path_factory):
    return tmp_path_factory.mktemp("google_map")


@pytest.fixture(scope="module")
def first(clean_platform, osm_files, folder):
    assert_completed(run_to_completion(start_run("osm.extract", {"country": "PK", "source_path": str(osm_files[1])},
                                                 trigger_type="manual", triggered_by="test")["id"]))
    # The scraper may be mid-write: the half-written last line is skipped until it is complete.
    write(folder / "google_map.jsonl", [AYLANTO, FAZAL, BIRYANI, BIRYANI_AGAIN, TWIN, MOSQUE, MOSQUE, CLOSED, GLITCH, NO_ID],
          tail='{"result_type": "list", "name": "Half wri')
    result = run(folder)
    assert_completed(result)
    return result


def places():
    return {p["source_record_id"]: p for p in query("silver.places", {"source": "googlemaps", "country": "PK"})}


def test_first_run_stores_each_record_once(first):
    artifact = repository.get_artifact(first["outputs"]["artifact_id"])
    meta = artifact["metadata"]
    assert (meta["records"], meta["read"], meta["repeated_in_input"], meta["unreadable"]) == (9, 10, 1, 1)
    bronze = query("bronze.google_places", {"country": "PK"})
    assert len(bronze) == 9 and len({b["record_hash"] for b in bronze}) == 9


def test_silver_has_one_row_per_place(first):
    rows = places()
    assert set(rows) == {"g-aylanto", "g-fazal", "ChIJCbiryani", "ChIJ_biryani", "g-glitch"}   # mosque excluded
    biryani = rows["ChIJCbiryani"]
    assert biryani["silver_id"] == "googlemaps:ChIJCbiryani"
    assert biryani["normalized_phone"] == "+924235111111" and biryani["category_group"] == "Food & Drink"
    attributes = json.loads(biryani["attributes"])
    assert attributes["search_queries"] == ["biryani in Lahore Pakistan", "restaurants in Lahore Pakistan"]
    assert (attributes["observations"], attributes["rating"], attributes["reviews"]) == (2, 4.5, 120)
    glitch = rows["g-glitch"]                     # an address in the category falls back to the searched category
    assert (glitch["category"], glitch["category_group"]) == ("pharmacies", "Health")
    rejects = {r["reason"]: r for r in query("silver.rejects", {"source": "googlemaps", "country": "PK"})}
    assert set(rejects) == {"permanently_closed", "missing_place_id"}


def test_gold_merges_with_osm_instead_of_duplicating(first):
    rows = serving("SELECT name, sources, phone, member_count FROM gold.businesses WHERE name IN "
                   "('Cafe Aylanto', 'Fazal Din Pharma', 'Lahore Biryani House')")
    by_name = {}
    for row in rows:
        by_name.setdefault(row["name"], []).append(row)
    assert {name: len(found) for name, found in by_name.items()} == \
        {"Cafe Aylanto": 1, "Fazal Din Pharma": 1, "Lahore Biryani House": 1}
    assert by_name["Cafe Aylanto"][0]["sources"] == ["googlemaps", "osm"]
    assert by_name["Fazal Din Pharma"][0]["sources"] == ["googlemaps", "osm"]
    assert by_name["Lahore Biryani House"][0]["sources"] == ["googlemaps"]      # Google's two listings: one business
    assert by_name["Lahore Biryani House"][0]["member_count"] == 2
    rated = serving("SELECT rating, review_count, field_sources->>'rating' AS rating_from FROM gold.businesses "
                    "WHERE name = 'Cafe Aylanto'")
    assert rated == [{"rating": 4.5, "review_count": 120, "rating_from": "googlemaps"}]   # OSM has no ratings


@pytest.fixture(scope="module")
def unchanged(first, folder):
    result = run(folder)
    assert_completed(result)
    return result


def test_rerun_without_new_records_changes_nothing(first, unchanged):
    stages = stage_map(unchanged["id"])
    assert stages["DOWNLOAD"]["status"] == "SKIPPED"
    assert unchanged["outputs"]["artifact_id"] == first["outputs"]["artifact_id"]
    assert stages["SILVER_TRANSFORM"]["status"] == "SKIPPED" and stages["GOLD_BUILD"]["status"] == "SKIPPED"
    assert len(repository.list_artifacts("googlemaps")) == 1


@pytest.fixture(scope="module")
def appended(unchanged, folder):
    rescraped = {**BIRYANI, "phone": "+92 42 35222222", "website": "https://lahorebiryani.pk", "address": None,
                 "search_query": "biryani in Lahore Pakistan"}
    newcomer = listing("g-new", "Gourmet Bakers", 32.0300, 74.0300, category="Bakery")
    with open(folder / "google_map.jsonl", "a") as handle:   # the half-written line stays unreadable
        handle.write("\n" + "".join(json.dumps(r) + "\n" for r in [rescraped, AYLANTO, newcomer]))
    write(folder / "more.jsonl", [FAZAL])                    # a second file repeating a stored record
    result = run(folder)
    assert_completed(result)
    return result


def test_appended_records_add_only_what_is_new(first, appended):
    artifact = repository.get_artifact(appended["outputs"]["artifact_id"])
    assert artifact["id"] != first["outputs"]["artifact_id"]
    assert (artifact["metadata"]["records"], artifact["metadata"]["already_stored"]) == (2, 12)
    assert len(query("bronze.google_places", {"country": "PK"})) == 11


def test_newest_observation_wins_per_field(appended):
    rows = places()
    assert set(rows) == {"g-aylanto", "g-fazal", "ChIJCbiryani", "ChIJ_biryani", "g-glitch", "g-new"}
    biryani = rows["ChIJCbiryani"]
    assert biryani["normalized_phone"] == "+924235222222"                    # updated by the newer scrape
    assert biryani["normalized_domain"] == "lahorebiryani.pk"               # new field filled in
    assert biryani["address"] == "12 Mall Rd"                               # kept: the newer scrape had none
    assert json.loads(biryani["attributes"])["observations"] == 3
    gold = serving("SELECT count(*) AS n FROM gold.businesses WHERE name = 'Lahore Biryani House'")
    assert gold[0]["n"] == 1
    assert serving("SELECT count(*) AS n FROM gold.businesses WHERE name = 'Gourmet Bakers'")[0]["n"] == 1
