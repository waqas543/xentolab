"""A tiny local Overture release (STAC catalog + GeoParquet) shaped like the real one.

Places sit in the same "Testland" as the OSM fixture so cross-source matching can be
tested: two businesses also mapped in OSM, one that adds a website OSM lacks, one
Overture-only business, plus a mosque (not a business), a closed shop (rejected), a
low-confidence kiosk (Silver only), an Indian record (filtered at download), and a
row group / file far away (pruned without being read).
"""

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import shapely

RELEASE = "2026-01-01.0"

SOURCE = pa.struct([("property", pa.string()), ("dataset", pa.string()), ("record_id", pa.string()),
                    ("update_time", pa.string()), ("confidence", pa.float64())])
ADDRESS = pa.struct([("freeform", pa.string()), ("locality", pa.string()), ("postcode", pa.string()),
                     ("region", pa.string()), ("country", pa.string())])
PLACE_SCHEMA = pa.schema([
    ("id", pa.string()),
    ("geometry", pa.binary()),
    ("bbox", pa.struct([("xmin", pa.float64()), ("xmax", pa.float64()), ("ymin", pa.float64()), ("ymax", pa.float64())])),
    ("version", pa.int32()),
    ("sources", pa.list_(SOURCE)),
    ("names", pa.struct([("primary", pa.string()), ("common", pa.map_(pa.string(), pa.string())),
                         ("rules", pa.list_(pa.struct([("variant", pa.string()), ("value", pa.string())])))])),
    ("taxonomy", pa.struct([("primary", pa.string()), ("hierarchy", pa.list_(pa.string())),
                            ("alternates", pa.list_(pa.string()))])),
    ("basic_category", pa.string()),
    ("confidence", pa.float64()),
    ("websites", pa.list_(pa.string())),
    ("socials", pa.list_(pa.string())),
    ("emails", pa.list_(pa.string())),
    ("phones", pa.list_(pa.string())),
    ("brand", pa.struct([("wikidata", pa.string()), ("names", pa.struct([("primary", pa.string())]))])),
    ("addresses", pa.list_(ADDRESS)),
    ("operating_status", pa.string()),
], metadata={b"geo": json.dumps({"version": "1.1.0", "primary_column": "geometry", "columns": {   # as in real releases
    "geometry": {"encoding": "WKB", "geometry_types": ["Point"], "covering": {"bbox": {
        "xmin": ["bbox", "xmin"], "ymin": ["bbox", "ymin"], "xmax": ["bbox", "xmax"], "ymax": ["bbox", "ymax"]}}}}}).encode()})


def place(pid, name, lat, lon, hierarchy, *, country="PK", confidence=0.9, websites=None, phones=None, emails=None,
          socials=None, status=None, locality="Lahore", common=None):
    return {
        "id": pid, "geometry": shapely.to_wkb(shapely.Point(lon, lat)),
        "bbox": {"xmin": lon, "xmax": lon, "ymin": lat, "ymax": lat}, "version": 1,
        "sources": [{"property": "", "dataset": "meta", "record_id": f"fb-{pid}", "update_time": "2026-08-01T00:00:00Z",
                     "confidence": confidence}],
        "names": {"primary": name, "common": common, "rules": None},
        "taxonomy": {"primary": hierarchy[-1], "hierarchy": hierarchy, "alternates": None} if hierarchy else None,
        "basic_category": hierarchy[-1] if hierarchy else None, "confidence": confidence, "websites": websites,
        "socials": socials, "emails": emails, "phones": phones, "brand": None,
        "addresses": [{"freeform": "Main Blvd", "locality": locality, "postcode": "54000", "region": None,
                       "country": country}],
        "operating_status": status,
    }


TESTLAND = [
    # Same restaurant as OSM n101 / w201 (same phone).
    place("ov-aylanto", "Cafe Aylanto", 32.00112, 74.00108, ["food_and_drink", "restaurant", "italian_restaurant"],
          websites=["https://aylanto.com"], phones=["+92 42 35781769"], socials=["https://www.facebook.com/aylanto"]),
    # Same company as OSM n107 "Systems (Pvt.) Limited" (name key "systems", same domain).
    place("ov-systems", "Systems Limited", 32.04005, 74.04005,
          ["services_and_business", "professional_service", "software_development"],
          websites=["https://www.systems-ltd.com/"], emails=["hello@systems-ltd.com"]),
    # Same pharmacy as OSM n110, which has no website: Overture enriches it.
    place("ov-fazaldin", "Fazal Din Pharma", 31.20108, 74.50105, ["health_care", "pharmacy"],
          websites=["fazaldin.com"], locality="Kasur"),
    # Overture-only business.
    place("ov-bookshop", "Readings Bookshop", 32.0800, 74.0800, ["shopping", "bookstore"], phones=["0300 7654321"]),
    # Not a business (same rule as OSM's place_of_worship).
    place("ov-mosque", "Badshahi Mosque", 32.0900, 74.0900, ["cultural_and_historic", "place_of_worship", "mosque"]),
    # Closed -> silver.rejects.
    place("ov-closed", "Old Video Shop", 32.0950, 74.0950, ["shopping", "electronics_store"], status="permanently_closed"),
    # Low confidence: kept in Silver, not promoted to Gold.
    place("ov-kiosk", "Mystery Kiosk", 32.0970, 74.0970, ["shopping", "kiosk"], confidence=0.12),
    # No taxonomy at all: kept as an uncategorized business.
    place("ov-unknown", "Unlabelled Place", 32.0980, 74.0980, None),
    # Inside the bbox but addressed in India: filtered out at download.
    place("ov-india", "Amritsar Dhaba", 31.6, 74.9, ["food_and_drink", "restaurant"], country="IN"),
]
FAR_AWAY = [place("ov-africa", "Far Away Cafe", 0.5, 20.5, ["food_and_drink", "cafe"], country="CF")]


def _write(path: Path, groups: list[list[dict]]):
    with pq.ParquetWriter(path, PLACE_SCHEMA) as writer:
        for rows in groups:            # each list becomes its own row group (with bbox statistics)
            writer.write_table(pa.Table.from_pylist(rows, schema=PLACE_SCHEMA))


def write_release(root: Path) -> str:
    """Create the release under `root`; returns the catalog URL to set as OVERTURE_STAC_URL."""
    data = root / "data"
    data.mkdir(parents=True, exist_ok=True)
    near, far = data / "places-0.parquet", data / "places-1.parquet"
    _write(near, [TESTLAND, FAR_AWAY])                  # second row group must be pruned by statistics
    _write(far, [FAR_AWAY])                             # whole file outside the country: never opened
    divisions = data / "division_area-0.parquet"
    pq.write_table(pa.table({
        "id": ["d-pk", "d-in"], "country": ["PK", "IN"], "subtype": ["country", "country"],
        "bbox": [{"xmin": 70.0, "xmax": 76.0, "ymin": 28.0, "ymax": 34.0},
                 {"xmin": 68.0, "xmax": 97.0, "ymin": 6.0, "ymax": 36.0}],
    }), divisions)

    def item(item_id, path, bbox, rows):
        return {"type": "Feature", "id": item_id, "bbox": bbox, "properties": {"num_rows": rows},
                "assets": {"local": {"href": path.as_uri(), "type": "application/vnd.apache.parquet"}}}

    write = lambda rel, obj: ((root / rel).parent.mkdir(parents=True, exist_ok=True),  # noqa: E731
                              (root / rel).write_text(json.dumps(obj)))
    write("catalog.json", {"type": "Catalog", "id": "Overture Releases", "latest": RELEASE, "links": [
        {"rel": "child", "href": f"{RELEASE}/catalog.json", "title": f"{RELEASE} Overture Release"}]})
    write(f"{RELEASE}/catalog.json", {"type": "Catalog", "id": RELEASE, "links": [
        {"rel": "child", "href": "places/catalog.json", "title": "places"},
        {"rel": "child", "href": "divisions/catalog.json", "title": "divisions"}]})
    write(f"{RELEASE}/places/catalog.json", {"links": [{"rel": "child", "href": "place/collection.json", "title": "place"}]})
    write(f"{RELEASE}/places/place/collection.json", {"type": "Collection", "id": "place", "table:row_count": 11, "links": [
        {"rel": "item", "href": "00000.json"}, {"rel": "item", "href": "00001.json"}]})
    write(f"{RELEASE}/places/place/00000.json", item("00000", near, [20.0, 0.0, 75.0, 33.0], 10))
    write(f"{RELEASE}/places/place/00001.json", item("00001", far, [20.0, 0.0, 21.0, 1.0], 1))
    write(f"{RELEASE}/divisions/catalog.json", {"links": [
        {"rel": "child", "href": "division_area/collection.json", "title": "division_area"}]})
    write(f"{RELEASE}/divisions/division_area/collection.json", {"links": [{"rel": "item", "href": "00000.json"}]})
    write(f"{RELEASE}/divisions/division_area/00000.json", item("d0", divisions, [68.0, 6.0, 97.0, 36.0], 2))
    return (root / "catalog.json").as_uri()
