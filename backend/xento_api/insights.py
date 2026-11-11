"""Insights for the mobile app: what the listings say about a market, a city, a brand or one business.

Raw counts mislead across cities: coverage differs (a city mapped in detail lists more of everything) and
population figures disagree between sources. So comparisons use each category's *share* of a city's listings
against its share nationally (a location quotient, shown as an index where 1.0 is typical). Per-person figures are
given as context only, where the population is known.

Everything here reads the pre-aggregated tables built by scripts/app_schema.sql (category_stats,
city_category_stats, country_stats) plus indexed lookups on app.places, and is cached per dataset version.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .db import one, rows

router = APIRouter(prefix="/api/app", tags=["app"])

MIN_CITY_PLACES = 200          # a city with fewer listings is too small to profile
MIN_COMPARE_PLACES = 1500      # cities compared in "crowded" / "room to grow" lists
MIN_TOP_RATED_REVIEWS = 20     # a "top rated" place needs this many reviews
MIN_BRANCH_REVIEWS = 10
NEARBY_KM = 1.0
GENERIC_CATEGORIES = ("coworking_office",)   # plus every other_* key: catch-alls say nothing about a market


def _specific(column: str = "category_key") -> str:
    # %% survives psycopg's parameter substitution as a single %.
    return f"({column} NOT LIKE 'other\\_%%' AND {column} NOT IN {GENERIC_CATEGORIES!r})".replace(",)", ")")


def _pct(part, whole) -> float | None:
    return round(100.0 * part / whole, 1) if whole else None


def _round(value, digits=2):
    return round(value, digits) if value is not None else None


def verdict(index: float | None) -> str | None:
    """Plain-language reading of a share index (1.0 = the national mix)."""
    if index is None:
        return None
    if index >= 1.5:
        return "much_more"
    if index >= 1.15:
        return "more"
    if index > 0.85:
        return "typical"
    if index > 0.5:
        return "fewer"
    return "much_fewer"


def default_country() -> str:
    row = one("SELECT country_code FROM app.country_stats ORDER BY places DESC LIMIT 1")
    if row is None:
        raise HTTPException(404, "No data")
    return row["country_code"]


def _country_totals(country: str) -> dict:
    return one("SELECT * FROM app.country_stats WHERE country_code = %(c)s", {"c": country}) or {}


def _places(where: str, params: dict, order: str, limit: int) -> list[dict]:
    from .routes import PLACE_COLUMNS, place
    return [place(r) for r in rows(
        f"SELECT {PLACE_COLUMNS} FROM app.places p WHERE {where} ORDER BY {order} LIMIT {int(limit)}", params)]


def _top_rated(where: str, params: dict, limit: int = 5) -> list[dict]:
    return _places(f"{where} AND p.review_count >= {MIN_TOP_RATED_REVIEWS} AND p.rating_score IS NOT NULL", params,
                   "p.rating_score DESC, p.review_count DESC, p.id", limit)


# ---------------------------------------------------------------- a category's market

@router.get("/insights/categories/{key}")
def category_market(key: str, city_id: str | None = None, country: str | None = None):
    """A category's market: how big, where it is crowded or thin, who leads it, how it rates, how online it is."""
    from .routes import cached

    city_row = None
    if city_id:
        city_row = one("SELECT id, name, country_code, places, population FROM app.cities WHERE id = %(id)s",
                       {"id": city_id})
        if city_row is None:
            raise HTTPException(404, "City not found")
    country = (city_row or {}).get("country_code") or country or default_country()
    return cached(f"market:{country}:{key}:{city_id}", lambda: _category_market(key, country, city_row))


def _category_market(key: str, country: str, city_row: dict | None) -> dict:
    params = {"key": key, "country": country}
    stats = one("SELECT * FROM app.category_stats WHERE country_code = %(country)s AND category_key = %(key)s", params)
    if stats is None:
        raise HTTPException(404, "Category not found")
    totals = _country_totals(country)
    national_share = stats["places"] / totals["places"]
    index = f"(s.places::float8 / NULLIF(c.places, 0)) / {national_share}"

    def city_list(order: str, where: str = "true", left: bool = False, limit: int = 6) -> list[dict]:
        join = "LEFT JOIN" if left else "JOIN"
        found = rows(f"""
            SELECT c.id, c.name, c.province, c.population, c.places AS city_places, coalesce(s.places, 0) AS places,
                   coalesce({index}, 0) AS idx
            FROM app.cities c {join} app.city_category_stats s ON s.city_id = c.id AND s.category_key = %(key)s
            WHERE c.country_code = %(country)s AND {where}
            ORDER BY {order} LIMIT {limit}
        """, params)
        return [{"id": r["id"], "name": r["name"], "province": r["province"], "places": r["places"],
                 "index": _round(r["idx"]), "verdict": verdict(r["idx"]),
                 "expected": round(r["city_places"] * national_share),
                 "per_10k_people": _round(10000.0 * r["places"] / r["population"]) if r["population"] else None}
                for r in found]

    big = f"c.places >= {MIN_COMPARE_PLACES}"
    result = {
        "category": {"key": key, "label": stats["label"], "group": stats["group_name"]},
        "country": country,
        "national": {
            "places": stats["places"], "cities": stats["cities"],
            "share_of_all_pct": _pct(stats["places"], totals["places"]),
            "with_phone_pct": _pct(stats["with_phone"], stats["places"]),
            "with_website_pct": _pct(stats["with_website"], stats["places"]),
            "with_social_pct": _pct(stats["with_social"], stats["places"]),
            "rated_pct": _pct(stats["rated"], stats["places"]),
            "avg_rating": _round(stats["avg_rating"]),
            "open_24h": stats["open_24h"],
            "in_chains_pct": _pct(stats["in_chains"], stats["places"]),
            # The same measures over every business, so a category can be read against the whole economy.
            "all_with_website_pct": _pct(totals["with_website"], totals["places"]),
            "all_with_phone_pct": _pct(totals["with_phone"], totals["places"]),
            "all_avg_rating": _round(totals["avg_rating"]),
        },
        "top_cities": city_list("s.places DESC, c.name"),
        "crowded": [c for c in city_list("idx DESC, c.name", big) if c["index"] >= 1.15],
        "room_to_grow": [c for c in city_list("idx ASC, c.places DESC", big, left=True) if c["index"] <= 0.85],
        "city": None,
    }

    scope, scope_params = "p.category_key = %(key)s AND p.country_code = %(country)s", dict(params)
    scope_total = stats["places"]
    if city_row:
        s = one("SELECT * FROM app.city_category_stats WHERE city_id = %(city)s AND category_key = %(key)s",
                {"city": city_row["id"], "key": key}) or {"places": 0, "with_phone": 0, "with_website": 0,
                                                           "rated": 0, "avg_rating": None, "open_24h": 0}
        idx = (s["places"] / city_row["places"]) / national_share if city_row["places"] else None
        rank = one(f"""
            SELECT count(*) + 1 AS rank FROM app.city_category_stats x JOIN app.cities c ON c.id = x.city_id
            WHERE x.category_key = %(key)s AND c.country_code = %(country)s AND x.places > %(n)s
        """, {**params, "n": s["places"]})["rank"]
        result["city"] = {
            "id": city_row["id"], "name": city_row["name"], "places": s["places"], "rank": rank,
            "share_of_all_pct": _pct(s["places"], city_row["places"]),
            "index": _round(idx), "verdict": verdict(idx),
            "expected": round(city_row["places"] * national_share),
            "per_10k_people": _round(10000.0 * s["places"] / city_row["population"]) if city_row["population"] else None,
            "with_phone_pct": _pct(s["with_phone"], s["places"]), "with_website_pct": _pct(s["with_website"], s["places"]),
            "rated_pct": _pct(s["rated"], s["places"]), "avg_rating": _round(s["avg_rating"]), "open_24h": s["open_24h"],
            "areas": rows("""
                SELECT area AS name, count(*)::int AS count FROM app.places
                WHERE city_id = %(city)s AND category_key = %(key)s AND area IS NOT NULL
                GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 8
            """, {"city": city_row["id"], "key": key}),
        }
        scope += " AND p.city_id = %(city)s"
        scope_params["city"] = city_row["id"]
        scope_total = s["places"]

    brands = rows(f"""
        SELECT o.id, o.name, count(*)::int AS locations
        FROM app.places p JOIN app.companies o ON o.id = p.company_id AND o.country_code = p.country_code
        WHERE {scope} GROUP BY 1, 2 ORDER BY 3 DESC, 2 LIMIT 8
    """, scope_params)
    result["brands"] = [{**b, "share_pct": _pct(b["locations"], scope_total)} for b in brands]
    result["top_rated"] = _top_rated(scope, scope_params)
    return result


# ---------------------------------------------------------------- a city's profile

def city_insights(city: dict) -> dict | None:
    """What a city stands out for, what it has fewer of than usual, how online and how well rated it is."""
    from .routes import cached

    if (city.get("places") or 0) < MIN_CITY_PLACES:
        return None
    return cached(f"city-insights:{city['id']}", lambda: _city_insights(city))


def _city_insights(city: dict) -> dict:
    country = city["country_code"]
    totals = _country_totals(country)
    params = {"city": city["id"], "country": country, "total": city["places"], "national": totals["places"]}
    share_index = "(x.places::float8 / %(total)s) / (s.places::float8 / %(national)s)"
    stands_out = rows(f"""
        SELECT s.category_key AS key, s.label, s.group_name AS "group", x.places, {share_index} AS idx
        FROM app.city_category_stats x
        JOIN app.category_stats s ON s.category_key = x.category_key AND s.country_code = %(country)s
        WHERE x.city_id = %(city)s AND x.places >= 10 AND {_specific('x.category_key')}
        ORDER BY idx DESC LIMIT 5
    """, params)
    gaps = rows(f"""
        SELECT s.category_key AS key, s.label, s.group_name AS "group", coalesce(x.places, 0) AS places,
               s.places::float8 / %(national)s * %(total)s AS expected
        FROM app.category_stats s
        LEFT JOIN app.city_category_stats x ON x.category_key = s.category_key AND x.city_id = %(city)s
        WHERE s.country_code = %(country)s AND {_specific('s.category_key')}
          AND s.places::float8 / %(national)s * %(total)s >= 5
          AND coalesce(x.places, 0) <= 0.6 * s.places::float8 / %(national)s * %(total)s
        ORDER BY s.places::float8 / %(national)s * %(total)s - coalesce(x.places, 0) DESC LIMIT 5
    """, params)
    here = one("""
        SELECT count(*)::int AS places, count(*) FILTER (WHERE has_phone)::int AS with_phone,
               count(*) FILTER (WHERE has_website)::int AS with_website,
               count(*) FILTER (WHERE social IS NOT NULL)::int AS with_social,
               count(rating_score)::int AS rated, avg(rating_score)::float8 AS avg_rating,
               count(*) FILTER (WHERE open_24_hours)::int AS open_24h
        FROM app.places WHERE city_id = %(city)s
    """, params)
    return {
        "stands_out": [{**r, "index": _round(r.pop("idx"))} for r in stands_out if r["idx"] >= 1.3],
        "fewer_than_usual": [{**r, "expected": round(r["expected"])} for r in gaps],
        "online": {"with_website_pct": _pct(here["with_website"], here["places"]),
                   "with_phone_pct": _pct(here["with_phone"], here["places"]),
                   "with_social_pct": _pct(here["with_social"], here["places"]),
                   "national_with_website_pct": _pct(totals["with_website"], totals["places"]),
                   "national_with_phone_pct": _pct(totals["with_phone"], totals["places"])},
        "ratings": {"rated": here["rated"], "avg_rating": _round(here["avg_rating"]),
                    "national_avg_rating": _round(totals["avg_rating"])},
        "open_24h": here["open_24h"],
        "top_rated": _top_rated("p.city_id = %(city)s", params),
    }


# ---------------------------------------------------------------- a brand's position

def organization_insights(organization_id: str, country: str | None) -> dict | None:
    """A brand's market position: share and rank in its category, rivals, branch ratings, where it is missing."""
    from .routes import cached

    return cached(f"org-insights:{organization_id}", lambda: _organization_insights(organization_id, country))


def _organization_insights(organization_id: str, country: str | None) -> dict | None:
    params = {"id": organization_id}
    main = one(f"""
        SELECT category_key, country_code, count(*)::int AS n FROM app.places
        WHERE company_id = %(id)s AND category_key IS NOT NULL
        GROUP BY 1, 2 ORDER BY {_specific()} DESC, 3 DESC LIMIT 1
    """, params)
    if main is None:
        return None
    country = main["country_code"] or country
    params.update(key=main["category_key"], country=country)
    stats = one("SELECT * FROM app.category_stats WHERE country_code = %(country)s AND category_key = %(key)s", params)
    branches = one("""
        SELECT count(*)::int AS locations, count(*) FILTER (WHERE has_phone)::int AS with_phone,
               count(*) FILTER (WHERE has_website OR website_shared)::int AS with_website,
               count(rating_score)::int AS rated, avg(rating_score)::float8 AS avg_rating
        FROM app.places WHERE company_id = %(id)s
    """, params)
    if branches["locations"] < 2:
        return None
    ranking = rows("""
        SELECT p.company_id AS id, max(o.name) AS name, count(*)::int AS locations
        FROM app.places p JOIN app.companies o ON o.id = p.company_id AND o.country_code = p.country_code
        WHERE p.category_key = %(key)s AND p.country_code = %(country)s
        GROUP BY p.company_id ORDER BY 3 DESC, 2
    """, params)
    rank = next((i + 1 for i, r in enumerate(ranking) if r["id"] == organization_id), None)
    competitors = [r for r in ranking if r["id"] != organization_id][:6]
    strongest = rows("""
        SELECT c.id, c.name, count(*)::int AS locations, max(x.places) AS category_places
        FROM app.places p JOIN app.cities c ON c.id = p.city_id
        JOIN app.city_category_stats x ON x.city_id = p.city_id AND x.category_key = %(key)s
        WHERE p.company_id = %(id)s AND p.category_key = %(key)s
        GROUP BY 1, 2 HAVING max(x.places) >= 10
        ORDER BY count(*)::float8 / max(x.places) DESC, 3 DESC LIMIT 5
    """, params)
    missing = rows("""
        SELECT c.id, c.name, x.places AS category_places,
               (SELECT count(DISTINCT q.company_id) FROM app.places q
                WHERE q.city_id = c.id AND q.category_key = %(key)s AND q.company_id IS NOT NULL)::int AS brands_present
        FROM app.city_category_stats x JOIN app.cities c ON c.id = x.city_id
        WHERE x.category_key = %(key)s AND c.country_code = %(country)s
          AND NOT EXISTS (SELECT 1 FROM app.places p WHERE p.company_id = %(id)s AND p.city_id = c.id)
        ORDER BY x.places DESC LIMIT 5
    """, params)
    rated = f"p.company_id = %(id)s AND p.review_count >= {MIN_BRANCH_REVIEWS} AND p.rating_score IS NOT NULL"
    best = _places(rated, params, "p.rating_score DESC, p.review_count DESC", 3)
    best_ids = {b["id"] for b in best}
    weakest = [b for b in _places(rated, params, "p.rating_score ASC, p.review_count DESC", 3) if b["id"] not in best_ids]
    return {
        "category": {"key": main["category_key"], "label": stats["label"] if stats else None,
                     "group": stats["group_name"] if stats else None},
        "locations_in_category": main["n"],
        "share_pct": _pct(main["n"], stats["places"]) if stats else None,
        "rank": rank, "brands_in_category": len(ranking),
        "competitors": competitors,
        "online": {"with_phone_pct": _pct(branches["with_phone"], branches["locations"]),
                   "with_website_pct": _pct(branches["with_website"], branches["locations"])},
        "ratings": {"rated": branches["rated"], "avg_rating": _round(branches["avg_rating"]),
                    "category_avg_rating": _round(stats["avg_rating"]) if stats else None,
                    "best": best, "weakest": weakest},
        "strongest_cities": [{"id": r["id"], "name": r["name"], "locations": r["locations"],
                              "share_pct": _pct(r["locations"], r["category_places"])} for r in strongest],
        "missing_from": missing,
    }


# ---------------------------------------------------------------- one business in context

def business_context(row: dict) -> dict:
    """Competition around a business and how its rating compares with its peers in the same city."""
    from .routes import POINT

    result: dict = {"similar_nearby": None, "rating_context": None}
    if row["category_key"] and row["lat"] is not None:
        params = {"id": row["id"], "key": row["category_key"], "lat": row["lat"], "lng": row["lng"],
                  "radius_m": NEARBY_KM * 1000, "radius_deg": NEARBY_KM / 90.0}
        near = f"""p.category_key = %(key)s AND p.id <> %(id)s AND p.geom IS NOT NULL
                   AND ST_DWithin(p.geom, {POINT}, %(radius_deg)s) AND ST_DistanceSphere(p.geom, {POINT}) <= %(radius_m)s"""
        total = one(f"SELECT count(*)::int AS n FROM app.places p WHERE {near}", params)["n"]
        from .routes import PLACE_COLUMNS, place
        items = [place(r) for r in rows(f"""
            SELECT {PLACE_COLUMNS}, ST_DistanceSphere(p.geom, {POINT}) / 1000.0 AS distance_km
            FROM app.places p WHERE {near} ORDER BY p.geom <-> {POINT} LIMIT 5
        """, params)]
        result["similar_nearby"] = {"radius_km": NEARBY_KM, "count": total, "items": items}
    if row.get("rating_score") is not None and row["category_key"] and row.get("city_id"):
        peers = one("""
            SELECT count(*)::int AS compared, count(*) FILTER (WHERE rating_score < %(score)s)::int AS below
            FROM app.places WHERE category_key = %(key)s AND city_id = %(city)s AND rating_score IS NOT NULL
              AND id <> %(id)s
        """, {"score": row["rating_score"], "key": row["category_key"], "city": row["city_id"], "id": row["id"]})
        if peers["compared"] >= 5:
            result["rating_context"] = {"compared": peers["compared"],
                                        "better_than_pct": round(100.0 * peers["below"] / peers["compared"])}
    return result


def home_insights(country: str | None = None) -> dict:
    """National headline numbers and the biggest specific markets, for the home screen."""
    country = country or default_country()
    totals = _country_totals(country)
    markets = rows(f"""
        SELECT category_key AS key, label, group_name AS "group", places, cities
        FROM app.category_stats WHERE country_code = %(c)s AND {_specific()}
        ORDER BY places DESC LIMIT 12
    """, {"c": country})
    return {
        "country": country,
        "with_website_pct": _pct(totals["with_website"], totals["places"]),
        "with_phone_pct": _pct(totals["with_phone"], totals["places"]),
        "rated": totals["rated"], "avg_rating": _round(totals["avg_rating"]), "open_24h": totals["open_24h"],
        "markets": markets,
    }
