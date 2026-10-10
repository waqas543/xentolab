"""Fixture releases for GeoNames, Wikidata, Common Crawl, GDELT and OpenCorporates, served locally.

Everything lives in the same Testland as the OSM fixture (Lahore at 32.0, 74.0; Kasur at 31.2, 74.5),
so cross-source behaviour is exercised: Wikidata's Systems Limited shares OSM n107's domain, the HBL
brand QID tagged on OSM n102/n103 is a Wikidata item, a GeoNames college carries a Wikidata id,
Common Crawl knows the businesses' .com sites and discovers new .pk domains, GDELT news mentions
Systems Limited and HBL, and OpenCorporates holds Systems Limited's registry record (the same one
Wikidata's OpenCorporates ID points to).
"""

from __future__ import annotations

import gzip
import io
import json
import re
import zipfile
from pathlib import Path
from urllib.parse import unquote_plus

COUNTRY_QID = "Q843"
OC_TOKEN = "test-token-123"


# ---------------------------------------------------------------- GeoNames

def _tsv(rows: list[list]) -> str:
    return "".join("\t".join("" if v is None else str(v) for v in row) + "\n" for row in rows)


def feature(gid, name, ascii_name, lat, lon, fclass, code, population=0, admin2="1001", alternates=""):
    return [gid, name, ascii_name, alternates, lat, lon, fclass, code, "PK", "", "04", admin2, "", "", population,
            "", 215, "Asia/Karachi", "2026-01-15"]


GEONAMES_FEATURES = [
    feature(1001, "Lahore", "Lahore", 32.0, 74.0, "P", "PPLA2", 11126285, alternates="Lahore,لاہور"),
    feature(1002, "Kasūr", "Kasur", 31.2, 74.5, "P", "PPL", 358409),
    feature(1003, "Testābād", "Testabad", 32.08, 74.08, "P", "PPL"),
    feature(1004, "Old Fort Town", "Old Fort Town", 32.3, 74.3, "P", "PPLQ"),          # abandoned: excluded
    feature(1005, "Model Town", "Model Town", 32.03, 74.03, "P", "PPLX"),
    feature(2001, "Pearl Test Hotel", "Pearl Test Hotel", 32.09, 74.09, "S", "HTL"),
    feature(2002, "Test College", "Test College", 32.07, 74.07, "S", "SCHC"),
    feature(2003, "Testabad Sugar Mills", "Testabad Sugar Mills", 31.5, 74.2, "S", "MLSG"),
    feature(2004, "Testabad Railway Station", "Testabad Railway Station", 32.081, 74.081, "S", "RSTN"),
    feature(3001, "Ravi River", "Ravi River", 32.1, 74.1, "H", "STM"),
]
GEONAMES_ALTERNATES = [
    [1, 1001, "ur", "لاہور", "1", "", "", "", "", ""],
    [2, 1001, "en", "Lahore", "1", "", "", "", "", ""],
    [3, 1001, "link", "https://en.wikipedia.org/wiki/Lahore", "", "", "", "", "", ""],
    [4, 2002, "wkdt", "Q300", "", "", "", "", "", ""],
    [5, 1002, "ur", "قصور", "", "", "", "", "", ""],
]


def _zip_bytes(name: str, text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, text)
    return buffer.getvalue()


def write_geonames(root: Path) -> None:
    dump = root / "geonames" / "dump"
    (dump / "alternatenames").mkdir(parents=True)
    (root / "geonames" / "zip").mkdir(parents=True)
    (dump / "PK.zip").write_bytes(_zip_bytes("PK.txt", _tsv(GEONAMES_FEATURES)))
    (dump / "alternatenames" / "PK.zip").write_bytes(_zip_bytes("PK.txt", _tsv(GEONAMES_ALTERNATES)))
    (root / "geonames" / "zip" / "PK.zip").write_bytes(_zip_bytes("PK.txt", _tsv([
        ["PK", "54000", "Lahore GPO", "Punjab", "04", "Lahore", "", "", "", 32.0, 74.0, 4]])))
    (dump / "admin1CodesASCII.txt").write_text(_tsv([
        ["PK.04", "Punjab", "Punjab", 1167710], ["PK.05", "Sindh", "Sindh", 1164807], ["IN.01", "Other", "Other", 1]]))
    (dump / "admin2Codes.txt").write_text(_tsv([["PK.04.1001", "Lāhore District", "Lahore District", 9001]]))
    (dump / "countryInfo.txt").write_text("# GeoNames country info\n#ISO\tISO3\t...\n" + _tsv([
        ["PK", "PAK", "586", "PK", "Pakistan", "Islamabad", 803940, 220892331, "AS", ".pk", "PKR", "Rupee", "92",
         "#####", "^(\\d{5})$", "ur-PK,en-PK", 1168579, "CN,AF,IR,IN", ""],
        ["DE", "DEU", "276", "GM", "Germany", "Berlin", 357021, 82927922, "EU", ".de", "EUR", "Euro", "49",
         "#####", "^(\\d{5})$", "de", 2921044, "CH,PL", ""]]))


# ---------------------------------------------------------------- Wikidata

def _claim(prop: str, value, kind: str = "wikibase-entityid", rank: str = "normal") -> dict:
    if kind == "wikibase-entityid":
        value = {"entity-type": "item", "id": value}
    return {"mainsnak": {"snaktype": "value", "property": prop, "datavalue": {"value": value, "type": kind}},
            "rank": rank, "type": "statement"}


def _entity(qid: str, label: str, claims: dict[str, list], *, ur: str | None = None, description: str | None = None,
            aliases: list[str] | None = None, revision: int = 1000) -> dict:
    labels = {"en": {"language": "en", "value": label}}
    if ur:
        labels["ur"] = {"language": "ur", "value": ur}
    return {"type": "item", "id": qid, "lastrevid": revision, "modified": "2026-08-22T06:55:37Z", "labels": labels,
            "descriptions": {"en": {"language": "en", "value": description}} if description else {},
            "aliases": {"en": [{"language": "en", "value": a} for a in aliases or []]},
            "claims": claims, "sitelinks": {}}


WIKIDATA_ENTITIES = {
    "Q100": _entity("Q100", "Systems Limited", {
        "P31": [_claim("P31", "Q4830453")], "P17": [_claim("P17", COUNTRY_QID)], "P452": [_claim("P452", "Q880371")],
        "P856": [_claim("P856", "https://www.systems-ltd.com/", "string")],
        "P1320": [_claim("P1320", "pk/1234", "external-id")], "P159": [_claim("P159", "Q1435")],
        "P571": [_claim("P571", {"time": "+1977-00-00T00:00:00Z", "precision": 9}, "time")],
        "P1128": [_claim("P1128", {"amount": "+6000", "unit": "1"}, "quantity")],
        "P2002": [_claim("P2002", "SystemsLtdPK", "external-id")],
    }, ur="سسٹمز لمیٹڈ", description="Pakistani IT services company", aliases=["Systems Ltd"]),
    "Q1": _entity("Q1", "Habib Bank Limited", {
        "P31": [_claim("P31", "Q22687")], "P17": [_claim("P17", COUNTRY_QID)],
        "P856": [_claim("P856", "https://www.hbl.com", "string")],
    }, description="Pakistani bank", aliases=["HBL"]),
    "Q300": _entity("Q300", "Test College", {
        "P31": [_claim("P31", "Q2385804")], "P17": [_claim("P17", COUNTRY_QID)],
        "P625": [_claim("P625", {"latitude": 32.0701, "longitude": 74.0701, "precision": 0.0001},
                        "globecoordinate")],
    }),
    "Q400": _entity("Q400", "Lonely Organization", {
        "P31": [_claim("P31", "Q4830453")], "P17": [_claim("P17", COUNTRY_QID)],
    }, description="Known only to Wikidata"),
}
WIKIDATA_LABELS = {"Q4830453": "business", "Q22687": "bank", "Q2385804": "educational institution",
                   "Q880371": "software industry", "Q1435": "Lahore", COUNTRY_QID: "Pakistan", "Q515": "city"}
ORG_ITEMS = {"Q100": "Q4830453", "Q300": "Q2385804", "Q400": "Q4830453"}     # P17 = Testland, organization classes


def _bindings(rows: list[dict]) -> bytes:
    return json.dumps({"results": {"bindings": [
        {k: {"type": "uri" if str(v).startswith("http") else "literal", "value": str(v)} for k, v in row.items()}
        for row in rows]}}).encode()


def sparql_route(method: str, query: dict, body: bytes):
    text = query.get("query", "")
    entity = "http://www.wikidata.org/entity/"
    if "wdt:P297" in text:
        rows = [{"c": entity + COUNTRY_QID}]
    elif "GROUP BY ?class" in text:
        rows = [{"class": entity + "Q4830453", "n": 2}, {"class": entity + "Q2385804", "n": 600},
                {"class": entity + "Q515", "n": 40}]
    elif "hint:gearing" in text:
        asked = set(re.findall(r"wd:(Q\d+)", text.split("}")[0]))
        rows = [{"class": entity + c} for c in sorted(asked & {"Q4830453", "Q2385804"})]
    elif "FILTER(?class IN" in text:                               # small classes, country first
        asked = set(re.findall(r"wd:(Q\d+)", text.split("IN", 1)[1]))
        rows = [{"item": entity + q, "class": entity + c} for q, c in ORG_ITEMS.items() if c in asked]
    elif 'hint:optimizer "None"' in text:                           # one large class, class first
        cls = re.search(r"wdt:P31 wd:(Q\d+)", text).group(1)
        rows = [{"item": entity + q} for q, c in ORG_ITEMS.items() if c == cls]
    elif "VALUES ?item" in text:
        asked = set(re.findall(r"wd:(Q\d+)", text))
        rows = [{"item": entity + q, "rev": WIKIDATA_ENTITIES[q]["lastrevid"]} for q in sorted(asked)
                if q in WIKIDATA_ENTITIES]
    else:
        return 400, "text/plain", b"unexpected query"
    return 200, "application/sparql-results+json", _bindings(rows)


def wbgetentities_route(method: str, query: dict, body: bytes):
    ids = query.get("ids", "").split("|")
    entities = {}
    for qid in ids:
        if query.get("props") == "labels":
            label = WIKIDATA_LABELS.get(qid) or WIKIDATA_ENTITIES.get(qid, {}).get("labels", {}).get("en", {}).get("value")
            entities[qid] = {"id": qid, "labels": {"en": {"language": "en", "value": label}}} if label \
                else {"id": qid, "missing": ""}
        else:
            entities[qid] = WIKIDATA_ENTITIES.get(qid, {"id": qid, "missing": ""})
    return 200, "application/json", json.dumps({"entities": entities}).encode()


# ---------------------------------------------------------------- Common Crawl

CRAWL = "CC-MAIN-2026-01"


def _capture(key: str, url: str, status: int = 200, languages: str = "eng", redirect: str | None = None,
             mime: str = "text/html") -> str:
    record = {"url": url, "mime": mime, "mime-detected": mime, "status": str(status), "digest": "ABC",
              "length": "1234", "offset": str(abs(hash(url)) % 100000), "filename": f"crawl-data/{CRAWL}/x.warc.gz",
              "languages": languages, "encoding": "UTF-8"}
    if redirect:
        record["redirect"] = redirect
    return f"{key} 20260105120000 {json.dumps(record)}"


CC_BLOCKS = [
    [_capture("com,aylanto)/", "https://aylanto.com/"),
     _capture("com,aylanto)/menu", "https://aylanto.com/menu"),
     _capture("com,example)/", "https://example.com/")],                               # not in scope
    [_capture("com,systems-ltd,www)/", "https://www.systems-ltd.com/", languages="eng,urd"),
     _capture("org,unrelated)/", "https://unrelated.org/")],                            # not in scope
    [_capture("pk,com,casino-spam)/", "https://casino-spam.com.pk/"),
     _capture("pk,com,newshop)/", "https://newshop.com.pk/", languages="urd,eng"),
     _capture("pk,com,newshop)/about", "https://newshop.com.pk/about", languages="urd"),
     _capture("pk,com,newshop,www)/", "http://www.newshop.com.pk/", status=301, languages="urd",
              redirect="https://newshop.com.pk/"),
     _capture("pk,edu,testuni)/", "https://testuni.edu.pk/"),
     _capture("pk,gov,punjab)/", "https://punjab.gov.pk/", status=503)],
    [_capture("pl,sklep)/", "https://sklep.pl/")],                                     # after .pk
]


def write_commoncrawl(root: Path) -> None:
    base = root / "cc" / "cc-index" / "collections"
    indexes = base / CRAWL / "indexes"
    indexes.mkdir(parents=True)
    (base / "index.html").write_text(f"<html><a href='{CRAWL}/'>{CRAWL}</a> <a>CC-MAIN-2025-51</a></html>")
    cdx, cluster, offset = b"", [], 0
    for number, lines in enumerate(CC_BLOCKS):
        member = gzip.compress(("\n".join(lines) + "\n").encode())
        first = lines[0].split(" ")
        cluster.append(f"{first[0]} {first[1]}\tcdx-00000.gz\t{offset}\t{len(member)}\t{number + 1}")
        cdx += member
        offset += len(member)
    (indexes / "cdx-00000.gz").write_bytes(cdx)
    (indexes / "cluster.idx").write_text("\n".join(cluster) + "\n")


# ---------------------------------------------------------------- GDELT

GDELT_LATEST = "20261009120000"


def gkg_row(record_id: str, source: str, url: str, locations: str, organizations: str,
            themes: str = "ECON_STOCKMARKET;TAX_FNCACT", tone: str = "2.5,3,0.5,3.5,20,0,300") -> str:
    columns = [record_id, record_id.split("-")[0], "1", source, url, "", "", themes, "", locations, "", "", "",
               organizations, "", tone, "", "wc:300", "", "", "", "", "", "", "", "", ""]
    assert len(columns) == 27
    return "\t".join(columns)


GDELT_FILES = {
    "20261009114500.gkg.csv.zip": [
        gkg_row("20261009114500-1", "example.com", "https://example.com/pk-tech",
                "4#Lahore, Punjab, Pakistan#PK#PK04#32#74#-1",
                "systems limited;associated press;habib bank limited;germany"),
        gkg_row("20261009114500-2", "example.in", "https://example.in/delhi", "1#India#IN#IN#20#77#IN", "tata group"),
    ],
    "20261009120000.gkg.csv.zip": [
        gkg_row("20261009120000-1", "tribune.com.pk", "https://tribune.com.pk/systems", "", "systems limited",
                tone="5,6,1,7,20,0,250"),
    ],
    "20261009120000.translation.gkg.csv.zip": [
        gkg_row("20261009120000-T1", "jang.com.pk", "https://jang.com.pk/hbl", "1#Pakistan#PK#PK#30#70#PK",
                "habib bank limited", tone="-1,1,2,3,20,0,100"),
    ],
}


def write_gdelt(root: Path) -> None:
    folder = root / "gdelt"
    folder.mkdir()
    (folder / "lastupdate.txt").write_text(
        f"100 abc http://data.gdeltproject.org/gdeltv2/{GDELT_LATEST}.export.CSV.zip\n"
        f"200 def http://data.gdeltproject.org/gdeltv2/{GDELT_LATEST}.gkg.csv.zip\n")
    # 11:15 and 11:30 English files are absent on purpose (GDELT sometimes skips an interval); so are the
    # translated 11:15-11:45 files.
    for name, rows in GDELT_FILES.items():
        (folder / name).write_bytes(_zip_bytes(name.removesuffix(".zip"), "\n".join(rows) + "\n"))


# ---------------------------------------------------------------- OpenCorporates

class OpenCorporatesAPI:
    """Search endpoint with a token check and a request quota (then 429), like the real API."""

    COMPANIES = {
        "systems": {"name": "SYSTEMS LIMITED", "company_number": "1234", "jurisdiction_code": "pk",
                    "incorporation_date": "1977-04-21", "company_type": "Public Limited Company",
                    "current_status": "Active", "registered_address_in_full": "E-1, Sehjpal, Lahore",
                    "opencorporates_url": "https://opencorporates.com/companies/pk/1234"},
        "habib bank": {"name": "HABIB BANK LIMITED", "company_number": "5678", "jurisdiction_code": "pk",
                       "incorporation_date": "1941-08-25", "company_type": "Public Limited Company",
                       "current_status": "Active",
                       "opencorporates_url": "https://opencorporates.com/companies/pk/5678"},
    }

    def __init__(self, quota: int = 2):
        self.quota = quota
        self.calls = 0

    def __call__(self, method: str, query: dict, body: bytes):
        if query.get("api_token") != OC_TOKEN:
            return 401, "application/json", b'{"error":{"message":"Invalid Api Token"}}'
        self.calls += 1
        if self.calls > self.quota:
            return 429, "application/json", b'{"error":{"message":"Rate limit exceeded"}}'
        q = unquote_plus(query.get("q", "")).lower()
        companies = [{"company": c} for key, c in self.COMPANIES.items() if key in q]
        return 200, "application/json", json.dumps({"api_version": "0.4", "results": {
            "companies": companies, "page": 1, "per_page": 5, "total_count": len(companies)}}).encode()


def write_all(root: Path) -> None:
    write_geonames(root)
    write_commoncrawl(root)
    write_gdelt(root)
