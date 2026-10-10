"""Consumer API for the mobile app: read-only views over the Gold serving tables.

The app shows the data, never where it came from. Every response is built from explicit column lists:
source names, source record ids, lineage, match confidence, per-field provenance and external identifiers
(Wikidata, gazetteer ids) never leave this module. City ids are hashed for the same reason.
"""

from __future__ import annotations

import math
import re
import time
from typing import Annotated, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Query

from .. import db

router = APIRouter(prefix="/api/app", tags=["app"])

CACHE_SECONDS = 300
_cache: dict[str, tuple[float, object]] = {}


def cached(key: str, build):
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    value = build()
    _cache[key] = (time.monotonic(), value)
    return value


def rows(sql: str, params: dict | None = None) -> list[dict]:
    with db.serving() as conn:
        return conn.execute(sql, params or {}).fetchall()


def one(sql: str, params: dict | None = None) -> dict | None:
    found = rows(sql, params)
    return found[0] if found else None


def page(items: list, total: int, offset: int, limit: int) -> dict:
    return {"items": items, "total": total, "next_offset": offset + limit if offset + limit < total else None}


def like(text: str) -> str:
    return "%" + re.sub(r"([\\%_])", r"\\\1", text.strip()) + "%"


# ---------------------------------------------------------------- labels

CATEGORY_LABELS = {
    "unspecified_shop": "Shop", "atm": "ATM", "fuel": "Fuel station", "college_university": "College / university",
    "doctors": "Doctor", "dentist": "Dentist", "it": "IT services", "ngo": "NGO", "cng": "CNG station",
}


def category_label(key: str | None) -> str | None:
    if not key:
        return None
    if key in CATEGORY_LABELS:
        return CATEGORY_LABELS[key]
    text = key.split(";")[0].strip()
    text = re.sub(r"^b2b_", "", text).replace("_", " ").replace(" / ", " / ").strip()
    return text[:1].upper() + text[1:] if text else None


WEBSITE_TYPES = {                       # app filter key -> (label, gold domain_type values)
    "business": ("Business", ["commercial", "known_business", "business"]),
    "education": ("Education", ["education"]),
    "government": ("Government", ["government"]),
    "nonprofit": ("Non-profit", ["non-profit"]),
    "general": ("General", ["generic", "network", "web"]),
}


def website_type(raw: str | None) -> tuple[str, str]:
    for key, (label, values) in WEBSITE_TYPES.items():
        if raw in values:
            return key, label
    return "other", "Other"


def url_for(website: str | None) -> str | None:
    if not website:
        return None
    return website if re.match(r"^https?://", website, re.I) else f"https://{website}"


CITY_ID = "'c' || left(md5(c.city_id), 15)"


def city_ids(names: list[str]) -> dict[str, str]:
    """Public city id for each business city name (the gold city row that holds that name's counts)."""
    names = [n for n in set(names) if n]
    if not names:
        return {}
    found = rows(f"SELECT DISTINCT ON (name) name, {CITY_ID} AS id FROM gold.cities c "
                 "WHERE name = ANY(%(names)s) AND business_count > 0 ORDER BY name, business_count DESC",
                 {"names": names})
    return {r["name"]: r["id"] for r in found}


# ---------------------------------------------------------------- businesses

BUSINESS_COLUMNS = """b.gold_id, b.name, b.category, b.category_group, b.city, b.locality, b.address, b.phone,
    b.website, b.email, b.latitude, b.longitude, b.brand_name, b.organization_id"""


def business(row: dict) -> dict:
    item = {
        "id": row["gold_id"], "name": row["name"],
        "category": category_label(row["category"]) or row["category_group"], "category_key": row["category"],
        "group": row["category_group"], "city": row["city"],
        "area": row["locality"] if row["locality"] and row["locality"] != row["city"] else None,
        "address": row["address"] or None, "phone": row["phone"] or None, "website": url_for(row["website"]),
        "email": row["email"] or None, "lat": row["latitude"], "lng": row["longitude"],
        "brand": row["brand_name"] or None, "organization_id": row["organization_id"],
    }
    if row.get("distance_km") is not None:
        item["distance_km"] = round(row["distance_km"], 2)
    return item


def near_clause(params: dict, lat: float, lng: float, radius_km: float) -> tuple[str, str]:
    """(WHERE fragment, distance expression). The degree box uses the GiST index; the sphere check is exact."""
    params.update(lat=lat, lng=lng, radius_m=radius_km * 1000,
                  radius_deg=radius_km / (111.32 * max(math.cos(math.radians(lat)), 0.2)))
    point = "ST_SetSRID(ST_MakePoint(%(lng)s, %(lat)s), 4326)"
    distance = f"ST_DistanceSphere(b.geom, {point}) / 1000.0"
    return f"ST_DWithin(b.geom, {point}, %(radius_deg)s) AND ST_DistanceSphere(b.geom, {point}) <= %(radius_m)s", distance


@router.get("/businesses")
def list_businesses(
    q: str | None = None,
    group: str | None = None,
    category: str | None = None,
    city: str | None = None,
    area: str | None = None,
    province: str | None = None,
    organization: str | None = None,
    website: str | None = None,
    has_phone: bool = False,
    has_website: bool = False,
    has_email: bool = False,
    lat: Annotated[float | None, Query(ge=-90, le=90)] = None,
    lng: Annotated[float | None, Query(ge=-180, le=180)] = None,
    radius_km: Annotated[float, Query(gt=0, le=100)] = 5,
    sort: Literal["best", "name", "distance"] = "best",
    offset: Annotated[int, Query(ge=0, le=10000)] = 0,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
):
    where, params = ["true"], {"offset": offset, "limit": limit}
    if q and q.strip():
        params.update(like=like(q), exact=q.strip(), prefix=like(q)[1:])
        where.append("(b.name ILIKE %(like)s OR b.brand_name ILIKE %(like)s)")
    for column, value in (("category_group", group), ("category", category), ("city", city), ("locality", area),
                          ("admin1", province), ("organization_id", organization), ("domain", website)):
        if value:
            params[column] = value
            where.append(f"b.{column} = %({column})s")
    for column, enabled in (("has_phone", has_phone), ("has_website", has_website), ("has_email", has_email)):
        if enabled:
            where.append(f"b.{column}")
    distance = "NULL::float"
    if lat is not None and lng is not None:
        clause, distance = near_clause(params, lat, lng, radius_km)
        where.append(clause)
    elif sort == "distance":
        raise HTTPException(422, "sort=distance needs lat and lng")

    if sort == "distance" or (lat is not None and lng is not None and sort == "best" and not q):
        order = "b.geom <-> ST_SetSRID(ST_MakePoint(%(lng)s, %(lat)s), 4326)"
    elif sort == "name":
        order = "b.name, b.gold_id"
    elif q and q.strip():
        order = "(lower(b.name) = lower(%(exact)s)) DESC, (b.name ILIKE %(prefix)s) DESC, b.completeness DESC, b.name, b.gold_id"
    else:
        order = "b.completeness DESC, b.name, b.gold_id"

    condition = " AND ".join(where)
    total = one(f"SELECT count(*) AS n FROM gold.businesses b WHERE {condition}", params)["n"]
    found = rows(f"""
        SELECT {BUSINESS_COLUMNS}, {distance} AS distance_km FROM gold.businesses b
        WHERE {condition} ORDER BY {order} OFFSET %(offset)s LIMIT %(limit)s
    """, params)
    return page([business(r) for r in found], total, offset, limit)


@router.get("/businesses/{business_id}")
def get_business(business_id: str):
    row = one(f"SELECT {BUSINESS_COLUMNS}, b.postal_code, b.admin1, b.admin2, b.domain FROM gold.businesses b "
              "WHERE b.gold_id = %(id)s", {"id": business_id})
    if row is None:
        raise HTTPException(404, "Place not found")
    item = business(row) | {"postal_code": row["postal_code"], "province": row["admin1"], "district": row["admin2"],
                            "city_id": city_ids([row["city"]]).get(row["city"])}
    item["organization"] = None
    if row["organization_id"]:
        item["organization"] = one("""
            SELECT organization_id AS id, name, industry, location_count AS locations FROM gold.organizations
            WHERE organization_id = %(id)s ORDER BY location_count DESC LIMIT 1
        """, {"id": row["organization_id"]})
    item["nearby"] = []
    if row["latitude"] is not None:
        params = {"id": business_id, "lat": row["latitude"], "lng": row["longitude"]}
        near = rows(f"""
            SELECT {BUSINESS_COLUMNS},
                   ST_DistanceSphere(b.geom, ST_SetSRID(ST_MakePoint(%(lng)s, %(lat)s), 4326)) / 1000.0 AS distance_km
            FROM gold.businesses b WHERE b.gold_id <> %(id)s AND b.geom IS NOT NULL
            ORDER BY b.geom <-> ST_SetSRID(ST_MakePoint(%(lng)s, %(lat)s), 4326) LIMIT 6
        """, params)
        item["nearby"] = [business(r) for r in near]
    return item


# ---------------------------------------------------------------- organizations

ORG_COLUMNS = """o.organization_id, o.name, o.industry, o.category_group, o.location_count, o.city_count,
    o.headquarters, o.website, o.domain, o.description"""


def organization(row: dict) -> dict:
    industry = row["industry"]
    return {
        "id": row["organization_id"], "name": row["name"],
        "industry": industry[:1].upper() + industry[1:] if industry else None, "group": row["category_group"],
        "locations": row["location_count"], "cities": row["city_count"], "headquarters": row["headquarters"],
        "website": url_for(row["website"] or row["domain"]), "description": row["description"],
    }


@router.get("/organizations")
def list_organizations(
    q: str | None = None,
    group: str | None = None,
    chains_only: bool = False,
    sort: Literal["locations", "name", "news"] = "locations",
    offset: Annotated[int, Query(ge=0, le=10000)] = 0,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
):
    where, params = ["true"], {"offset": offset, "limit": limit}
    if q and q.strip():
        params.update(like=like(q), prefix=like(q)[1:])
        where.append("(o.name ILIKE %(like)s OR o.legal_name ILIKE %(like)s "
                     "OR EXISTS (SELECT 1 FROM unnest(o.aliases) a WHERE a ILIKE %(like)s))")
    if group:
        params["group"] = group
        where.append("o.category_group = %(group)s")
    if chains_only:
        where.append("o.location_count >= 2")
    order = {"locations": "o.location_count DESC, o.news_mentions DESC", "name": "o.name",
             "news": "o.news_mentions DESC, o.location_count DESC"}[sort]
    if q and q.strip() and sort == "locations":
        order = "(o.name ILIKE %(prefix)s) DESC, " + order
    condition = " AND ".join(where)
    total = one(f"SELECT count(*) AS n FROM gold.organizations o WHERE {condition}", params)["n"]
    found = rows(f"SELECT {ORG_COLUMNS} FROM gold.organizations o WHERE {condition} "
                 f"ORDER BY {order}, o.organization_id OFFSET %(offset)s LIMIT %(limit)s", params)
    return page([organization(r) for r in found], total, offset, limit)


@router.get("/organizations/{organization_id}")
def get_organization(organization_id: str):
    row = one(f"""
        SELECT {ORG_COLUMNS}, o.legal_name, o.inception, o.employees, o.parent_name, o.aliases, o.registry_id,
               o.registry_status, o.news_mentions, o.news_last_seen
        FROM gold.organizations o WHERE o.organization_id = %(id)s ORDER BY o.location_count DESC LIMIT 1
    """, {"id": organization_id})
    if row is None:
        raise HTTPException(404, "Company not found")
    cities = rows("""
        SELECT city AS name, count(*)::int AS locations FROM gold.businesses
        WHERE organization_id = %(id)s AND city IS NOT NULL GROUP BY city ORDER BY 2 DESC, 1 LIMIT 10
    """, {"id": organization_id})
    ids = city_ids([c["name"] for c in cities])
    founded = re.match(r"^-?\d{3,4}", row["inception"] or "")
    return organization(row) | {
        "legal_name": row["legal_name"] if row["legal_name"] != row["name"] else None,
        "founded": founded.group(0) if founded else None, "employees": row["employees"],
        "parent": row["parent_name"], "also_known_as": [a for a in (row["aliases"] or []) if a != row["name"]][:6],
        "registration_number": row["registry_id"], "status": row["registry_status"],
        "news_mentions": row["news_mentions"] or 0, "last_in_news": row["news_last_seen"],
        "top_cities": [c | {"id": ids.get(c["name"])} for c in cities],
    }


# ---------------------------------------------------------------- cities

CITY_COLUMNS = f"""{CITY_ID} AS id, c.name, c.name_local, c.kind, c.admin1, c.admin2, c.population, c.business_count,
    c.with_phone, c.with_website, c.organizations, c.businesses_per_10k, c.top_category_group, c.latitude, c.longitude"""


def city(row: dict) -> dict:
    return {
        "id": row["id"], "name": row["name"],
        "local_name": row["name_local"] if row["name_local"] != row["name"] else None,
        "kind": (row["kind"] or "place").capitalize(), "province": row["admin1"], "district": row["admin2"],
        "population": row["population"], "businesses": row["business_count"], "with_phone": row["with_phone"],
        "with_website": row["with_website"], "companies": row["organizations"],
        "per_10k_people": row["businesses_per_10k"], "top_group": row["top_category_group"],
        "lat": row["latitude"], "lng": row["longitude"],
    }


@router.get("/cities")
def list_cities(
    q: str | None = None,
    province: str | None = None,
    sort: Literal["businesses", "population", "name"] = "businesses",
    offset: Annotated[int, Query(ge=0, le=20000)] = 0,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
):
    where, params = ["c.business_count > 0"], {"offset": offset, "limit": limit}
    if q and q.strip():
        params["like"] = like(q)
        where.append("(c.name ILIKE %(like)s OR c.name_local ILIKE %(like)s)")
    if province:
        params["province"] = province
        where.append("c.admin1 = %(province)s")
    order = {"businesses": "c.business_count DESC", "population": "c.population DESC NULLS LAST",
             "name": "c.name"}[sort]
    condition = " AND ".join(where)
    total = one(f"SELECT count(*) AS n FROM gold.cities c WHERE {condition}", params)["n"]
    found = rows(f"SELECT {CITY_COLUMNS} FROM gold.cities c WHERE {condition} "
                 f"ORDER BY {order}, c.name, c.city_id OFFSET %(offset)s LIMIT %(limit)s", params)
    return page([city(r) for r in found], total, offset, limit)


@router.get("/cities/{city_id}")
def get_city(city_id: str):
    row = one(f"SELECT {CITY_COLUMNS} FROM gold.cities c WHERE {CITY_ID} = %(id)s", {"id": city_id})
    if row is None:
        raise HTTPException(404, "City not found")
    params = {"name": row["name"]}
    groups = rows("""
        SELECT category_group AS name, count(*)::int AS count FROM gold.businesses
        WHERE city = %(name)s AND category_group IS NOT NULL GROUP BY 1 ORDER BY 2 DESC
    """, params)
    categories = rows("""
        SELECT category AS key, count(*)::int AS count FROM gold.businesses
        WHERE city = %(name)s AND category IS NOT NULL AND category <> '' GROUP BY 1 ORDER BY 2 DESC LIMIT 12
    """, params)
    areas = rows("""
        SELECT locality AS name, count(*)::int AS count FROM gold.businesses
        WHERE city = %(name)s AND locality IS NOT NULL AND locality <> city GROUP BY 1 ORDER BY 2 DESC LIMIT 15
    """, params)
    brands = rows("""
        SELECT o.organization_id AS id, o.name, count(*)::int AS locations
        FROM gold.businesses b JOIN gold.organizations o ON o.organization_id = b.organization_id
                                                        AND o.country_code = b.country_code
        WHERE b.city = %(name)s GROUP BY 1, 2 ORDER BY 3 DESC, 2 LIMIT 10
    """, params)
    return city(row) | {
        "groups": groups, "areas": areas, "brands": brands,
        "categories": [{"key": c["key"], "label": category_label(c["key"]), "count": c["count"]} for c in categories],
    }


# ---------------------------------------------------------------- websites

def website(row: dict) -> dict:
    kind, label = website_type(row["domain_type"])
    homepage = None
    if row["homepage_url"] and 200 <= (row["homepage_status"] or 0) < 400:
        parts = urlsplit(row["homepage_url"])                 # the site's root, not the page that was fetched
        homepage = f"{parts.scheme}://{parts.netloc}/" if parts.scheme and parts.netloc else None
    return {
        "domain": row["domain"], "url": homepage or f"https://{row['domain']}", "type": kind, "type_label": label,
        "businesses": row["business_count"], "group": row["top_category_group"],
        "organization": {"id": row["organization_id"], "name": row["organization_name"]}
        if row["organization_id"] and row["organization_name"] else None,
    }


WEBSITE_SELECT = """
    SELECT d.domain, d.domain_type, d.business_count, d.top_category_group, d.homepage_url, d.homepage_status,
           d.organization_id, (SELECT o.name FROM gold.organizations o WHERE o.organization_id = d.organization_id
                               ORDER BY o.location_count DESC LIMIT 1) AS organization_name
    FROM gold.domains d
"""


@router.get("/websites")
def list_websites(
    q: str | None = None,
    type: Literal["business", "education", "government", "nonprofit", "general", "other"] | None = None,
    sort: Literal["popular", "name"] = "popular",
    offset: Annotated[int, Query(ge=0, le=10000)] = 0,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
):
    where, params = ["true"], {"offset": offset, "limit": limit}
    if q and q.strip():
        params["like"] = like(q)
        where.append("d.domain ILIKE %(like)s")
    if type == "other":
        params["known"] = [v for _, values in WEBSITE_TYPES.values() for v in values]
        where.append("(d.domain_type IS NULL OR NOT d.domain_type = ANY(%(known)s))")
    elif type:
        params["types"] = WEBSITE_TYPES[type][1]
        where.append("d.domain_type = ANY(%(types)s)")
    # Popularity ranks by linked places, then web footprint; the footprint itself is not exposed.
    order = ("d.business_count DESC, d.crawl_pages DESC NULLS LAST" if sort == "popular" else "d.domain")
    condition = " AND ".join(where)
    total = one(f"SELECT count(*) AS n FROM gold.domains d WHERE {condition}", params)["n"]
    found = rows(f"{WEBSITE_SELECT} WHERE {condition} ORDER BY {order}, d.domain OFFSET %(offset)s LIMIT %(limit)s",
                 params)
    return page([website(r) for r in found], total, offset, limit)


@router.get("/websites/{domain}")
def get_website(domain: str):
    row = one(f"{WEBSITE_SELECT} WHERE d.domain = %(domain)s ORDER BY d.business_count DESC LIMIT 1",
              {"domain": domain.lower()})
    if row is None:
        raise HTTPException(404, "Website not found")
    return website(row)


# ---------------------------------------------------------------- overview, categories, search

def all_categories() -> list[dict]:
    def build():
        found = rows("""
            SELECT category AS key, mode() WITHIN GROUP (ORDER BY category_group) AS group_name, count(*)::int AS count
            FROM gold.businesses WHERE category IS NOT NULL AND category <> '' GROUP BY 1 ORDER BY 3 DESC
        """)
        return [{"key": r["key"], "label": category_label(r["key"]), "group": r["group_name"], "count": r["count"]}
                for r in found]
    return cached("categories", build)


@router.get("/home")
def home():
    def build():
        totals = one("""
            SELECT (SELECT count(*) FROM gold.businesses) AS businesses,
                   (SELECT count(*) FROM gold.organizations) AS companies,
                   (SELECT count(*) FROM gold.cities WHERE business_count > 0) AS cities,
                   (SELECT count(*) FROM gold.domains) AS websites,
                   (SELECT count(*) FROM gold.businesses WHERE has_phone) AS with_phone,
                   (SELECT max(built_at) FROM gold.businesses) AS updated_at
        """)
        groups = rows("""
            SELECT category_group AS name, count(*)::int AS count FROM gold.businesses
            WHERE category_group IS NOT NULL GROUP BY 1 ORDER BY 2 DESC
        """)
        provinces = rows("""
            SELECT admin1 AS name, count(*)::int AS count FROM gold.businesses
            WHERE admin1 IS NOT NULL AND admin1 <> '' GROUP BY 1 ORDER BY 2 DESC
        """)
        cities = rows(f"SELECT {CITY_COLUMNS} FROM gold.cities c WHERE c.business_count > 0 "
                      "ORDER BY c.business_count DESC LIMIT 10")
        brands = rows(f"SELECT {ORG_COLUMNS} FROM gold.organizations o WHERE o.location_count >= 2 "
                      "ORDER BY o.location_count DESC LIMIT 12")
        return {"totals": totals, "groups": groups, "provinces": provinces,
                "top_cities": [city(r) for r in cities], "top_companies": [organization(r) for r in brands]}
    return cached("home", build)


@router.get("/categories")
def list_categories(group: str | None = None, q: str | None = None, limit: Annotated[int, Query(ge=1, le=500)] = 60):
    found = all_categories()
    if group:
        found = [c for c in found if c["group"] == group]
    if q and q.strip():
        needle = q.strip().lower()
        found = [c for c in found if needle in c["label"].lower()]
    return found[:limit]


@router.get("/search")
def search(q: Annotated[str, Query(min_length=1, max_length=100)]):
    return {
        "categories": list_categories(q=q, limit=4),
        "cities": list_cities(q=q, limit=4)["items"],
        "businesses": list_businesses(q=q, limit=6)["items"],
        "organizations": list_organizations(q=q, limit=4)["items"],
        "websites": list_websites(q=q, limit=3)["items"],
    }
