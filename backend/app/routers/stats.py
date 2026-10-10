from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg import sql

from app import db
from app.filters import COMPANIES, CONTACT_FLAGS, DIMENSIONS, CompanyFilters, company_filters
from app.serialize import to_json_value

router = APIRouter(prefix="/api/stats", tags=["stats"])

Filters = Annotated[CompanyFilters, Depends(company_filters)]


def json_rows(rows):
    return [{key: to_json_value(value) for key, value in row.items()} for row in rows]


def dimension(name):
    if name not in DIMENSIONS:
        raise HTTPException(400, f"Unknown dimension {name}")
    return sql.Identifier(name)


def grouped(filters: CompanyFilters, column: str, limit: int | None = None):
    where, params = filters.where(extra=[f"{column} IS NOT NULL"])
    query = sql.SQL("""
        SELECT {col} AS label, count(*) AS count
        FROM {table} {where}
        GROUP BY 1 ORDER BY 2 DESC, 1
        {limit}
    """).format(
        col=sql.Identifier(column),
        table=COMPANIES,
        where=where,
        limit=sql.SQL("LIMIT {}").format(limit) if limit else sql.SQL(""),
    )
    return json_rows(db.fetch_all(query, params))


@router.get("/overview")
def overview(filters: Filters):
    where, params = filters.where()

    kpis = db.fetch_one(sql.SQL("""
        SELECT count(*) AS total,
               count(*) FILTER (WHERE has_phone) AS with_phone,
               count(*) FILTER (WHERE has_website) AS with_website,
               count(*) FILTER (WHERE has_email) AS with_email,
               count(*) FILTER (WHERE has_address) AS with_address,
               count(*) FILTER (WHERE has_opening_hours) AS with_opening_hours,
               count(*) FILTER (WHERE brand IS NOT NULL) AS branded,
               round(avg(completeness), 1) AS avg_completeness,
               count(DISTINCT brand) AS brands,
               count(DISTINCT city) AS cities,
               count(DISTINCT category) AS categories,
               max(last_edited) AS latest_edit
        FROM {} {}
    """).format(COMPANIES, where), params)

    top_categories = db.fetch_all(sql.SQL("""
        SELECT category AS label, category_group AS "group", count(*) AS count
        FROM {} {}
        GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 15
    """).format(COMPANIES, where), params)

    completeness = db.fetch_all(sql.SQL("""
        SELECT s.score AS label, count(c.completeness) AS count
        FROM generate_series(0, 100, 20) AS s(score)
        LEFT JOIN (SELECT completeness FROM {} {}) c ON c.completeness = s.score
        GROUP BY 1 ORDER BY 1
    """).format(COMPANIES, where), params)

    edits_by_year = db.fetch_all(sql.SQL("""
        SELECT extract(year FROM last_edited)::int AS label, count(*) AS count
        FROM {} {}
        GROUP BY 1 ORDER BY 1
    """).format(COMPANIES, filters.where(extra=["last_edited IS NOT NULL"])[0]), params)

    return {
        "kpis": to_json_value(kpis),
        "by_group": grouped(filters, "category_group"),
        "by_province": grouped(filters, "province"),
        "top_cities": grouped(filters, "city", 15),
        "top_categories": json_rows(top_categories),
        "completeness": json_rows(completeness),
        "edits_by_year": json_rows(edits_by_year),
    }


@router.get("/breakdown")
def breakdown(filters: Filters, by: str = "category", limit: Annotated[int, Query(ge=1, le=500)] = 50):
    dimension(by)
    return grouped(filters, by, limit)


@router.get("/pivot")
def pivot(
    filters: Filters,
    rows: str = "city",
    cols: str = "category_group",
    row_limit: Annotated[int, Query(ge=1, le=100)] = 20,
    col_limit: Annotated[int, Query(ge=1, le=40)] = 12,
):
    """Count matrix of the top `row_limit` x `col_limit` values of two dimensions."""
    if rows == cols:
        raise HTTPException(400, "Pick two different dimensions")

    row_col, col_col = dimension(rows), dimension(cols)
    where, params = filters.where(extra=[f"{rows} IS NOT NULL", f"{cols} IS NOT NULL"])

    result = db.fetch_all(sql.SQL("""
        WITH base AS (
            SELECT {r} AS r, {c} AS c FROM {table} {where}
        ), top_rows AS (
            SELECT r, count(*) AS total FROM base GROUP BY r ORDER BY 2 DESC, 1 LIMIT %(row_limit)s
        ), top_cols AS (
            SELECT c, count(*) AS total FROM base GROUP BY c ORDER BY 2 DESC, 1 LIMIT %(col_limit)s
        )
        SELECT b.r AS row, b.c AS col, count(*) AS count
        FROM base b
        JOIN top_rows USING (r)
        JOIN top_cols USING (c)
        GROUP BY 1, 2
    """).format(r=row_col, c=col_col, table=COMPANIES, where=where),
        {**params, "row_limit": row_limit, "col_limit": col_limit})

    row_totals = {}
    col_totals = {}
    cells = {}

    for item in result:
        row_totals[item["row"]] = row_totals.get(item["row"], 0) + item["count"]
        col_totals[item["col"]] = col_totals.get(item["col"], 0) + item["count"]
        cells[(item["row"], item["col"])] = item["count"]

    row_labels = sorted(row_totals, key=lambda key: (-row_totals[key], str(key)))
    col_labels = sorted(col_totals, key=lambda key: (-col_totals[key], str(key)))

    return {
        "rows": [to_json_value(label) for label in row_labels],
        "cols": [to_json_value(label) for label in col_labels],
        "row_totals": [row_totals[label] for label in row_labels],
        "col_totals": [col_totals[label] for label in col_labels],
        "matrix": [[cells.get((r, c), 0) for c in col_labels] for r in row_labels],
    }


@router.get("/brands")
def brands(filters: Filters, limit: Annotated[int, Query(ge=1, le=500)] = 100):
    where, params = filters.where(extra=["brand IS NOT NULL"])

    return json_rows(db.fetch_all(sql.SQL("""
        SELECT brand,
               count(*) AS locations,
               count(DISTINCT city) AS cities,
               count(DISTINCT province) AS provinces,
               mode() WITHIN GROUP (ORDER BY category_group) AS category_group,
               mode() WITHIN GROUP (ORDER BY category) AS category,
               mode() WITHIN GROUP (ORDER BY city) AS top_city,
               round(avg(completeness), 1) AS avg_completeness,
               round(100.0 * avg(has_phone::int), 1) AS pct_phone
        FROM {} {}
        GROUP BY brand
        ORDER BY locations DESC, brand
        LIMIT %(limit)s
    """).format(COMPANIES, where), {**params, "limit": limit}))


@router.get("/quality")
def quality(filters: Filters, by: str = "category_group", limit: Annotated[int, Query(ge=1, le=200)] = 30):
    """Contact-data coverage (% of companies with each field), per value of a dimension."""
    by_col = dimension(by)
    where, params = filters.where(extra=[f"{by} IS NOT NULL"])

    coverage = sql.SQL(", ").join(
        sql.SQL("round(100.0 * avg({}::int), 1) AS {}").format(sql.Identifier(flag), sql.Identifier(flag))
        for flag in CONTACT_FLAGS
    )

    return json_rows(db.fetch_all(sql.SQL("""
        SELECT {by} AS label, count(*) AS count, {coverage},
               round(avg(completeness), 1) AS avg_completeness
        FROM {table} {where}
        GROUP BY 1 ORDER BY 2 DESC, 1
        LIMIT %(limit)s
    """).format(by=by_col, coverage=coverage, table=COMPANIES, where=where),
        {**params, "limit": limit}))


@router.get("/duplicates")
def duplicates(
    filters: Filters,
    distance_m: Annotated[int, Query(ge=1, le=2000)] = 150,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
):
    """Pairs of companies with the same name within `distance_m` of each other."""
    where, params = filters.where()

    return json_rows(db.fetch_all(sql.SQL("""
        WITH filtered AS (
            SELECT osm_type, osm_id, name, lower(name) AS key, category, city, geom
            FROM {table} {where}
        ), repeated AS (
            SELECT key FROM filtered GROUP BY key HAVING count(*) > 1
        ), candidates AS (
            SELECT f.* FROM filtered f JOIN repeated USING (key)
        )
        SELECT a.name,
               a.osm_type AS a_type, a.osm_id AS a_id, a.category AS a_category,
               b.osm_type AS b_type, b.osm_id AS b_id, b.category AS b_category,
               a.city,
               round(ST_DistanceSphere(a.geom, b.geom)::numeric) AS distance_m
        FROM candidates a
        JOIN candidates b
          ON a.key = b.key
         AND (a.osm_type, a.osm_id) < (b.osm_type, b.osm_id)
        WHERE ST_DistanceSphere(a.geom, b.geom) <= %(distance_m)s
        ORDER BY distance_m, a.name
        LIMIT %(limit)s
    """).format(table=COMPANIES, where=where), {**params, "distance_m": distance_m, "limit": limit}))


@router.get("/tags")
def tag_keys(filters: Filters, limit: Annotated[int, Query(ge=1, le=1000)] = 200):
    """How often each OSM tag key occurs among the filtered companies."""
    where, params = filters.where()

    return json_rows(db.fetch_all(sql.SQL("""
        SELECT key AS label, count(*) AS count
        FROM {} c CROSS JOIN LATERAL jsonb_object_keys(c.tags) AS key
        {}
        GROUP BY 1 ORDER BY 2 DESC, 1
        LIMIT %(limit)s
    """).format(COMPANIES, where), {**params, "limit": limit}))


@router.get("/tags/{key:path}")
def tag_values(key: str, filters: Filters, limit: Annotated[int, Query(ge=1, le=1000)] = 100):
    where, params = filters.where(extra=["tags ? %(tag_key)s"])

    return json_rows(db.fetch_all(sql.SQL("""
        SELECT tags->>%(tag_key)s AS label, count(*) AS count
        FROM {} {}
        GROUP BY 1 ORDER BY 2 DESC, 1
        LIMIT %(limit)s
    """).format(COMPANIES, where), {**params, "tag_key": key, "limit": limit}))
