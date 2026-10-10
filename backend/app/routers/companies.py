import csv
import io
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from psycopg import sql
from psycopg.rows import tuple_row

from app import db
from app.filters import COMPANIES, CompanyFilters, company_filters
from app.serialize import to_csv_value, to_json_value

router = APIRouter(prefix="/api", tags=["companies"])

LIST_COLUMNS = [
    "osm_type", "osm_id", "name", "name_local", "category_group", "category", "brand",
    "phone", "website", "email", "opening_hours", "addr_street", "addr_housenumber",
    "addr_postcode", "addr_city", "city", "locality", "district", "province",
    "latitude", "longitude", "completeness", "last_edited",
]

EXPORT_COLUMNS = LIST_COLUMNS + ["operator", "country", "version", "last_editor"]

SORTABLE = {
    "name", "category_group", "category", "brand", "city", "locality", "district", "province",
    "completeness", "last_edited", "osm_id",
}


def json_rows(rows):
    return [{key: to_json_value(value) for key, value in row.items()} for row in rows]


def column_list(columns):
    return sql.SQL(", ").join(sql.Identifier(c) for c in columns)


@router.get("/filters")
def filter_options():
    """Distinct values (with counts) for the dashboard's filter controls."""
    groups = db.fetch_all("""
        SELECT category_group AS value, count(*) AS count
        FROM osm_app.companies GROUP BY 1 ORDER BY 2 DESC
    """)
    categories = db.fetch_all("""
        SELECT category AS value, category_group AS parent, count(*) AS count
        FROM osm_app.companies GROUP BY 1, 2 ORDER BY 3 DESC
    """)
    provinces = db.fetch_all("""
        SELECT province AS value, count(*) AS count
        FROM osm_app.companies WHERE province IS NOT NULL GROUP BY 1 ORDER BY 2 DESC
    """)
    districts = db.fetch_all("""
        SELECT district AS value, province AS parent, count(*) AS count
        FROM osm_app.companies WHERE district IS NOT NULL GROUP BY 1, 2 ORDER BY 3 DESC
    """)
    cities = db.fetch_all("""
        SELECT city AS value, count(*) AS count
        FROM osm_app.companies WHERE city IS NOT NULL GROUP BY 1 ORDER BY 2 DESC
    """)
    brands = db.fetch_all("""
        SELECT brand AS value, count(*) AS count
        FROM osm_app.companies WHERE brand IS NOT NULL GROUP BY 1 ORDER BY 2 DESC
    """)

    return {
        "groups": groups,
        "categories": categories,
        "provinces": provinces,
        "districts": districts,
        "cities": cities,
        "brands": brands,
    }


@router.get("/companies")
def list_companies(
    filters: Annotated[CompanyFilters, Depends(company_filters)],
    sort: str = "name",
    order: Literal["asc", "desc"] = "asc",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=500)] = 50,
):
    if sort not in SORTABLE:
        raise HTTPException(400, f"Cannot sort by {sort}")

    where, params = filters.where()
    direction = sql.SQL("DESC NULLS LAST" if order == "desc" else "ASC NULLS LAST")

    total = db.fetch_one(
        sql.SQL("SELECT count(*) AS total FROM {} {}").format(COMPANIES, where), params,
    )["total"]

    rows = db.fetch_all(
        sql.SQL("""
            SELECT {columns} FROM {table} {where}
            ORDER BY {sort} {direction}, osm_type, osm_id
            LIMIT %(limit)s OFFSET %(offset)s
        """).format(
            columns=column_list(LIST_COLUMNS),
            table=COMPANIES,
            where=where,
            sort=sql.Identifier(sort),
            direction=direction,
        ),
        {**params, "limit": page_size, "offset": (page - 1) * page_size},
    )

    return {"total": total, "page": page, "page_size": page_size, "rows": json_rows(rows)}


@router.get("/companies/export")
def export_companies(filters: Annotated[CompanyFilters, Depends(company_filters)]):
    where, params = filters.where()
    query = sql.SQL("SELECT {columns} FROM {table} {where} ORDER BY name").format(
        columns=column_list(EXPORT_COLUMNS), table=COMPANIES, where=where,
    )

    def generate():
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(EXPORT_COLUMNS)

        with db.pool.connection() as connection:
            with connection.transaction():
                with connection.cursor(name="export_companies") as cur:
                    cur.execute(query, params)

                    while batch := cur.fetchmany(5000):
                        for row in batch:
                            writer.writerow([to_csv_value(row[c]) for c in EXPORT_COLUMNS])

                        yield buffer.getvalue()
                        buffer.seek(0)
                        buffer.truncate()

    return StreamingResponse(
        generate(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="companies.csv"'},
    )


@router.get("/companies/{osm_type}/{osm_id}")
def company_detail(osm_type: Literal["n", "w", "r"], osm_id: int):
    company = db.fetch_one("""
        SELECT c.*, ST_AsGeoJSON(c.geom)::json AS geometry
        FROM osm_app.companies c
        WHERE osm_type = %s AND osm_id = %s
    """, [osm_type, osm_id])

    if company is None:
        raise HTTPException(404, "Company not found")

    company.pop("geom", None)

    nearby = db.fetch_all("""
        SELECT o.osm_type, o.osm_id, o.name, o.category_group, o.category,
               round(ST_DistanceSphere(o.geom, c.geom)::numeric) AS distance_m
        FROM osm_app.companies c
        CROSS JOIN LATERAL (
            SELECT * FROM osm_app.companies o
            WHERE (o.osm_type, o.osm_id) <> (c.osm_type, c.osm_id)
            ORDER BY o.geom <-> c.geom
            LIMIT 10
        ) o
        WHERE c.osm_type = %s AND c.osm_id = %s
    """, [osm_type, osm_id])

    return {
        "company": {k: to_json_value(v) for k, v in company.items()},
        "nearby": json_rows(nearby),
    }


@router.get("/map/extent")
def map_extent(filters: Annotated[CompanyFilters, Depends(company_filters)]):
    """Bounds of the filtered companies (1st-99th percentile, so stray points don't zoom the map out)."""
    where, params = filters.where()

    row = db.fetch_one(sql.SQL("""
        SELECT count(*) AS count,
               percentile_cont(0.01) WITHIN GROUP (ORDER BY longitude) AS west,
               percentile_cont(0.01) WITHIN GROUP (ORDER BY latitude) AS south,
               percentile_cont(0.99) WITHIN GROUP (ORDER BY longitude) AS east,
               percentile_cont(0.99) WITHIN GROUP (ORDER BY latitude) AS north
        FROM {} {}
    """).format(COMPANIES, where), params)

    return row if row["count"] else {"count": 0}


@router.get("/map/points")
def map_points(
    filters: Annotated[CompanyFilters, Depends(company_filters)],
    west: float | None = None,
    south: float | None = None,
    east: float | None = None,
    north: float | None = None,
    limit: Annotated[int, Query(ge=1, le=50_000)] = 20_000,
):
    """Compact points for the map: [osm_type, osm_id, lat, lon, name, group, category]."""
    extra = []

    if None not in (west, south, east, north):
        extra.append("geom && ST_MakeEnvelope(%(west)s, %(south)s, %(east)s, %(north)s, 4326)")

    where, params = filters.where(extra=extra)
    params.update({"west": west, "south": south, "east": east, "north": north, "limit": limit + 1})

    with db.pool.connection() as connection:
        with connection.cursor(row_factory=tuple_row) as cur:
            cur.execute(
                sql.SQL("""
                    SELECT osm_type, osm_id, round(latitude::numeric, 6)::float8,
                           round(longitude::numeric, 6)::float8, name, category_group, category
                    FROM {} {}
                    ORDER BY completeness DESC, osm_id
                    LIMIT %(limit)s
                """).format(COMPANIES, where),
                params,
            )
            rows = cur.fetchall()

    return {
        "truncated": len(rows) > limit,
        "points": [list(row) for row in rows[:limit]],
    }
