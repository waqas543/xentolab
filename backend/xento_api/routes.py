"""Read-only endpoints for the mobile app, over the `app` schema.

The `app` schema is built by scripts/app_schema.sql and already holds display-ready values (category labels,
normalized URLs, opaque city ids) and nothing about where the data came from. These queries only shape it.
"""

from __future__ import annotations

import math
import time
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query

from .db import one, rows

router = APIRouter(prefix="/api/app", tags=["app"])

CACHE_SECONDS = 300
_cache: dict[str, tuple[float, str, object]] = {}


def cached(key: str, build):
    """Cache per dataset version, so a restore shows up immediately."""
    version = one("SELECT version FROM app.dataset_info")["version"]
    hit = _cache.get(key)
    if hit and hit[1] == version and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[2]
    value = build()
    _cache[key] = (time.monotonic(), version, value)
    return value


def page(items: list, total: int, offset: int, limit: int) -> dict:
    return {"items": items, "total": total, "next_offset": offset + limit if offset + limit < total else None}


def like(text: str) -> str:
    escaped = text.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


Country = Annotated[str | None, Query(min_length=2, max_length=2, description="ISO country code, e.g. PK")]
Offset = Annotated[int, Query(ge=0, le=20000)]
Limit = Annotated[int, Query(ge=1, le=50)]

# ---------------------------------------------------------------- places

PLACE_COLUMNS = """p.id, p.name, p.category, p.category_key, p.group_name, p.city, p.area, p.address, p.phone,
    p.website, p.email, p.lat, p.lng, p.brand, p.company_id"""
POINT = "ST_SetSRID(ST_MakePoint(%(lng)s, %(lat)s), 4326)"


def place(row: dict) -> dict:
    item = {
        "id": row["id"], "name": row["name"], "category": row["category"], "category_key": row["category_key"],
        "group": row["group_name"], "city": row["city"], "area": row["area"], "address": row["address"],
        "phone": row["phone"], "website": row["website"], "email": row["email"], "lat": row["lat"], "lng": row["lng"],
        "brand": row["brand"], "organization_id": row["company_id"],
    }
    if row.get("distance_km") is not None:
        item["distance_km"] = round(row["distance_km"], 2)
    return item


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
    country: Country = None,
    has_phone: bool = False,
    has_website: bool = False,
    has_email: bool = False,
    lat: Annotated[float | None, Query(ge=-90, le=90)] = None,
    lng: Annotated[float | None, Query(ge=-180, le=180)] = None,
    radius_km: Annotated[float, Query(gt=0, le=100)] = 5,
    sort: Literal["best", "name", "distance"] = "best",
    offset: Offset = 0,
    limit: Limit = 20,
):
    where, params = ["true"], {"offset": offset, "limit": limit}
    searching = bool(q and q.strip())
    if searching:
        params.update(like=like(q), exact=q.strip(), prefix=like(q)[1:])
        where.append("(p.name ILIKE %(like)s OR p.brand ILIKE %(like)s)")
    for column, value in (("group_name", group), ("category_key", category), ("city", city), ("area", area),
                          ("province", province), ("company_id", organization), ("domain", website),
                          ("country_code", country)):
        if value:
            params[column] = value
            where.append(f"p.{column} = %({column})s")
    for column, enabled in (("has_phone", has_phone), ("has_website", has_website), ("has_email", has_email)):
        if enabled:
            where.append(f"p.{column}")

    near = lat is not None and lng is not None
    distance = "NULL::float"
    if near:
        # The degree box uses the GiST index; the sphere distance is exact.
        params.update(lat=lat, lng=lng, radius_m=radius_km * 1000,
                      radius_deg=radius_km / (111.32 * max(math.cos(math.radians(lat)), 0.2)))
        distance = f"ST_DistanceSphere(p.geom, {POINT}) / 1000.0"
        where.append(f"ST_DWithin(p.geom, {POINT}, %(radius_deg)s) AND ST_DistanceSphere(p.geom, {POINT}) <= %(radius_m)s")
    elif sort == "distance":
        raise HTTPException(422, "sort=distance needs lat and lng")

    if sort == "distance" or (near and sort == "best" and not searching):
        order = f"p.geom <-> {POINT}"
    elif sort == "name":
        order = "p.name, p.id"
    elif searching:
        order = "(lower(p.name) = lower(%(exact)s)) DESC, (p.name ILIKE %(prefix)s) DESC, p.completeness DESC, p.name, p.id"
    else:
        order = "p.completeness DESC, p.name, p.id"

    condition = " AND ".join(where)
    total = one(f"SELECT count(*) AS n FROM app.places p WHERE {condition}", params)["n"]
    found = rows(f"""
        SELECT {PLACE_COLUMNS}, {distance} AS distance_km FROM app.places p
        WHERE {condition} ORDER BY {order} OFFSET %(offset)s LIMIT %(limit)s
    """, params)
    return page([place(r) for r in found], total, offset, limit)


@router.get("/businesses/{business_id}")
def get_business(business_id: str):
    row = one(f"SELECT {PLACE_COLUMNS}, p.postal_code, p.province, p.district, p.city_id "
              "FROM app.places p WHERE p.id = %(id)s", {"id": business_id})
    if row is None:
        raise HTTPException(404, "Place not found")
    item = place(row) | {"postal_code": row["postal_code"], "province": row["province"],
                         "district": row["district"], "city_id": row["city_id"], "organization": None, "nearby": []}
    if row["company_id"]:
        item["organization"] = one("""
            SELECT id, name, industry, locations FROM app.companies
            WHERE id = %(id)s ORDER BY locations DESC LIMIT 1
        """, {"id": row["company_id"]})
    if row["lat"] is not None:
        params = {"id": business_id, "lat": row["lat"], "lng": row["lng"]}
        item["nearby"] = [place(r) for r in rows(f"""
            SELECT {PLACE_COLUMNS}, ST_DistanceSphere(p.geom, {POINT}) / 1000.0 AS distance_km
            FROM app.places p WHERE p.id <> %(id)s AND p.geom IS NOT NULL
            ORDER BY p.geom <-> {POINT} LIMIT 6
        """, params)]
    return item


# ---------------------------------------------------------------- companies

COMPANY_COLUMNS = "o.id, o.name, o.industry, o.group_name, o.locations, o.cities, o.headquarters, o.website, o.description"


def company(row: dict) -> dict:
    return {
        "id": row["id"], "name": row["name"], "industry": row["industry"], "group": row["group_name"],
        "locations": row["locations"], "cities": row["cities"], "headquarters": row["headquarters"],
        "website": row["website"], "description": row["description"],
    }


@router.get("/organizations")
def list_organizations(
    q: str | None = None,
    group: str | None = None,
    country: Country = None,
    chains_only: bool = False,
    sort: Literal["locations", "name", "news"] = "locations",
    offset: Offset = 0,
    limit: Limit = 20,
):
    where, params = ["true"], {"offset": offset, "limit": limit}
    searching = bool(q and q.strip())
    if searching:
        params.update(like=like(q), prefix=like(q)[1:])
        where.append("(o.name ILIKE %(like)s OR o.legal_name ILIKE %(like)s "
                     "OR EXISTS (SELECT 1 FROM unnest(o.aliases) a WHERE a ILIKE %(like)s))")
    if group:
        params["group"] = group
        where.append("o.group_name = %(group)s")
    if country:
        params["country"] = country
        where.append("o.country_code = %(country)s")
    if chains_only:
        where.append("o.locations >= 2")
    order = {"locations": "o.locations DESC, o.news_mentions DESC", "name": "o.name",
             "news": "o.news_mentions DESC, o.locations DESC"}[sort]
    if searching and sort == "locations":
        order = "(o.name ILIKE %(prefix)s) DESC, " + order
    condition = " AND ".join(where)
    total = one(f"SELECT count(*) AS n FROM app.companies o WHERE {condition}", params)["n"]
    found = rows(f"SELECT {COMPANY_COLUMNS} FROM app.companies o WHERE {condition} "
                 f"ORDER BY {order}, o.id OFFSET %(offset)s LIMIT %(limit)s", params)
    return page([company(r) for r in found], total, offset, limit)


@router.get("/organizations/{organization_id}")
def get_organization(organization_id: str):
    row = one(f"""
        SELECT {COMPANY_COLUMNS}, o.legal_name, o.founded, o.employees, o.parent, o.aliases, o.registration_number,
               o.status, o.news_mentions, o.last_in_news
        FROM app.companies o WHERE o.id = %(id)s ORDER BY o.locations DESC LIMIT 1
    """, {"id": organization_id})
    if row is None:
        raise HTTPException(404, "Company not found")
    cities = rows("""
        SELECT city AS name, max(city_id) AS id, count(*)::int AS locations FROM app.places
        WHERE company_id = %(id)s AND city IS NOT NULL GROUP BY city ORDER BY 3 DESC, 1 LIMIT 10
    """, {"id": organization_id})
    return company(row) | {
        "legal_name": row["legal_name"], "founded": row["founded"], "employees": row["employees"],
        "parent": row["parent"], "also_known_as": row["aliases"] or [],
        "registration_number": row["registration_number"], "status": row["status"],
        "news_mentions": row["news_mentions"] or 0, "last_in_news": row["last_in_news"],
        "top_cities": [{"id": c["id"], "name": c["name"], "locations": c["locations"]} for c in cities],
    }


# ---------------------------------------------------------------- cities

CITY_COLUMNS = """c.id, c.name, c.local_name, c.kind, c.province, c.district, c.population, c.places, c.with_phone,
    c.with_website, c.companies, c.per_10k_people, c.top_group, c.lat, c.lng"""


def city(row: dict) -> dict:
    return {
        "id": row["id"], "name": row["name"], "local_name": row["local_name"], "kind": row["kind"],
        "province": row["province"], "district": row["district"], "population": row["population"],
        "businesses": row["places"], "with_phone": row["with_phone"], "with_website": row["with_website"],
        "companies": row["companies"], "per_10k_people": row["per_10k_people"], "top_group": row["top_group"],
        "lat": row["lat"], "lng": row["lng"],
    }


@router.get("/cities")
def list_cities(
    q: str | None = None,
    province: str | None = None,
    country: Country = None,
    sort: Literal["businesses", "population", "name"] = "businesses",
    offset: Offset = 0,
    limit: Limit = 20,
):
    where, params = ["true"], {"offset": offset, "limit": limit}
    if q and q.strip():
        params["like"] = like(q)
        where.append("(c.name ILIKE %(like)s OR c.local_name ILIKE %(like)s)")
    if province:
        params["province"] = province
        where.append("c.province = %(province)s")
    if country:
        params["country"] = country
        where.append("c.country_code = %(country)s")
    order = {"businesses": "c.places DESC", "population": "c.population DESC NULLS LAST", "name": "c.name"}[sort]
    condition = " AND ".join(where)
    total = one(f"SELECT count(*) AS n FROM app.cities c WHERE {condition}", params)["n"]
    found = rows(f"SELECT {CITY_COLUMNS} FROM app.cities c WHERE {condition} "
                 f"ORDER BY {order}, c.name, c.id OFFSET %(offset)s LIMIT %(limit)s", params)
    return page([city(r) for r in found], total, offset, limit)


@router.get("/cities/{city_id}")
def get_city(city_id: str):
    row = one(f"SELECT {CITY_COLUMNS}, c.country_code FROM app.cities c WHERE c.id = %(id)s", {"id": city_id})
    if row is None:
        raise HTTPException(404, "City not found")
    params = {"name": row["name"], "country": row["country_code"]}
    in_city = "city = %(name)s AND country_code = %(country)s"
    groups = rows(f"""
        SELECT group_name AS name, count(*)::int AS count FROM app.places
        WHERE {in_city} AND group_name IS NOT NULL GROUP BY 1 ORDER BY 2 DESC
    """, params)
    categories = rows(f"""
        SELECT category_key AS key, min(category) AS label, count(*)::int AS count FROM app.places
        WHERE {in_city} AND category_key IS NOT NULL GROUP BY 1 ORDER BY 3 DESC LIMIT 12
    """, params)
    areas = rows(f"""
        SELECT area AS name, count(*)::int AS count FROM app.places
        WHERE {in_city} AND area IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 15
    """, params)
    brands = rows(f"""
        SELECT o.id, o.name, count(*)::int AS locations
        FROM app.places p JOIN app.companies o ON o.id = p.company_id AND o.country_code = p.country_code
        WHERE p.city = %(name)s AND p.country_code = %(country)s GROUP BY 1, 2 ORDER BY 3 DESC, 2 LIMIT 10
    """, params)
    return city(row) | {"groups": groups, "areas": areas, "brands": brands, "categories": categories}


# ---------------------------------------------------------------- websites

WEBSITE_SELECT = """
    SELECT w.domain, w.url, w.type, w.type_label, w.places, w.group_name, w.company_id,
           (SELECT o.name FROM app.companies o WHERE o.id = w.company_id ORDER BY o.locations DESC LIMIT 1) AS company_name
    FROM app.websites w
"""


def website(row: dict) -> dict:
    return {
        "domain": row["domain"], "url": row["url"], "type": row["type"], "type_label": row["type_label"],
        "businesses": row["places"], "group": row["group_name"],
        "organization": {"id": row["company_id"], "name": row["company_name"]}
        if row["company_id"] and row["company_name"] else None,
    }


@router.get("/websites")
def list_websites(
    q: str | None = None,
    type: Literal["business", "education", "government", "nonprofit", "general", "other"] | None = None,
    country: Country = None,
    sort: Literal["popular", "name"] = "popular",
    offset: Offset = 0,
    limit: Limit = 20,
):
    where, params = ["true"], {"offset": offset, "limit": limit}
    if q and q.strip():
        params["like"] = like(q)
        where.append("w.domain ILIKE %(like)s")
    if type:
        params["type"] = type
        where.append("w.type = %(type)s")
    if country:
        params["country"] = country
        where.append("w.country_code = %(country)s")
    order = "w.popularity" if sort == "popular" else "w.domain"
    condition = " AND ".join(where)
    total = one(f"SELECT count(*) AS n FROM app.websites w WHERE {condition}", params)["n"]
    found = rows(f"{WEBSITE_SELECT} WHERE {condition} ORDER BY {order}, w.domain OFFSET %(offset)s LIMIT %(limit)s",
                 params)
    return page([website(r) for r in found], total, offset, limit)


@router.get("/websites/{domain}")
def get_website(domain: str):
    row = one(f"{WEBSITE_SELECT} WHERE w.domain = %(domain)s ORDER BY w.places DESC LIMIT 1", {"domain": domain.lower()})
    if row is None:
        raise HTTPException(404, "Website not found")
    return website(row)


# ---------------------------------------------------------------- home, categories, search

def all_categories() -> list[dict]:
    return cached("categories", lambda: rows("""
        SELECT category_key AS key, min(category) AS label, mode() WITHIN GROUP (ORDER BY group_name) AS "group",
               count(*)::int AS count
        FROM app.places WHERE category_key IS NOT NULL GROUP BY 1 ORDER BY 4 DESC
    """))


@router.get("/home")
def home():
    def build():
        totals = one("""
            SELECT (SELECT count(*) FROM app.places) AS businesses,
                   (SELECT count(*) FROM app.companies) AS companies,
                   (SELECT count(*) FROM app.cities) AS cities,
                   (SELECT count(*) FROM app.websites) AS websites,
                   (SELECT count(*) FROM app.places WHERE has_phone) AS with_phone,
                   (SELECT data_updated_at FROM app.dataset_info) AS updated_at
        """)
        groups = rows("""
            SELECT group_name AS name, count(*)::int AS count FROM app.places
            WHERE group_name IS NOT NULL GROUP BY 1 ORDER BY 2 DESC
        """)
        provinces = rows("""
            SELECT province AS name, count(*)::int AS count FROM app.places
            WHERE province IS NOT NULL GROUP BY 1 ORDER BY 2 DESC
        """)
        cities = rows(f"SELECT {CITY_COLUMNS} FROM app.cities c ORDER BY c.places DESC LIMIT 10")
        brands = rows(f"SELECT {COMPANY_COLUMNS} FROM app.companies o WHERE o.locations >= 2 "
                      "ORDER BY o.locations DESC LIMIT 12")
        return {"totals": totals, "groups": groups, "provinces": provinces,
                "top_cities": [city(r) for r in cities], "top_companies": [company(r) for r in brands]}
    return cached("home", build)


@router.get("/categories")
def list_categories(group: str | None = None, q: str | None = None, limit: Annotated[int, Query(ge=1, le=500)] = 60):
    found = all_categories()
    if group:
        found = [c for c in found if c["group"] == group]
    if q and q.strip():
        needle = q.strip().lower()
        found = [c for c in found if needle in (c["label"] or "").lower()]
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
