"""SQL query runner.

Read-only mode (the default) runs inside a READ ONLY transaction that is always
rolled back, and sends the statement over the extended protocol, which rejects
multiple statements - so `COMMIT; DROP ...` cannot escape the transaction.
SELECT-like statements stream through a server-side cursor, so only the rows
shown are ever transferred, even for tables with millions of rows.

Write mode uses the simple protocol (several statements allowed), shows the
result of the last statement and commits.
"""

import csv
import io
import re
import threading
import time
import uuid
from typing import Annotated

import psycopg
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse
from psycopg.rows import tuple_row
from pydantic import BaseModel, Field

from app import db
from app.serialize import to_csv_value, to_json_value

router = APIRouter(prefix="/api/query", tags=["query"])

STREAMABLE = {"select", "with", "values", "table"}

_running: dict[str, psycopg.Connection] = {}
_running_lock = threading.Lock()


class RunRequest(BaseModel):
    sql: str
    read_only: bool = True
    max_rows: int = Field(default=1000, ge=1, le=100_000)
    timeout_seconds: int = Field(default=60, ge=1, le=3600)
    query_id: str | None = None


class ExplainRequest(BaseModel):
    sql: str
    analyze: bool = False
    timeout_seconds: int = Field(default=60, ge=1, le=3600)


class ExportRequest(BaseModel):
    sql: str
    read_only: bool = True
    timeout_seconds: int = Field(default=300, ge=1, le=3600)


class SavedQuery(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    sql: str


def clean_sql(text: str) -> str:
    cleaned = text.strip().rstrip(";").strip()
    if not cleaned:
        raise HTTPException(400, "Query is empty")
    return cleaned


LEADING_NOISE = re.compile(r"\s+|--[^\n]*|/\*.*?\*/|\(", re.S)


def first_keyword(text: str) -> str:
    """The first SQL keyword, skipping whitespace, comments and opening parentheses."""
    position = 0
    while match := LEADING_NOISE.match(text, position):
        position = match.end()
    keyword = re.match(r"[A-Za-z]+", text[position:])
    return keyword.group(0).lower() if keyword else ""


def error_payload(error: Exception, status: str = "error"):
    diag = getattr(error, "diag", None)

    return {
        "status": status,
        "message": (diag.message_primary if diag and diag.message_primary else str(error)).strip(),
        "detail": diag.message_detail if diag else None,
        "hint": diag.message_hint if diag else None,
        "position": int(diag.statement_position) if diag and diag.statement_position else None,
        "sqlstate": diag.sqlstate if diag else None,
    }


def column_types(connection, description):
    """Readable type names for result columns (includes extension types like geometry)."""
    oids = [column.type_code for column in description]
    names = {}

    for oid in oids:
        info = connection.adapters.types.get(oid)
        if info:
            names[oid] = info.name

    unknown = [oid for oid in oids if oid not in names]

    if unknown:
        with connection.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT oid, format_type(oid, NULL) FROM pg_type WHERE oid = ANY(%s)", [unknown])
            names.update(dict(cur.fetchall()))

    return [{"name": column.name, "type": names.get(column.type_code, "unknown")} for column in description]


def open_connection(read_only: bool, timeout_seconds: int):
    connection = db.connect(autocommit=False)
    connection.read_only = read_only

    with connection.cursor() as cur:
        cur.execute("SELECT set_config('statement_timeout', %s, true)", [f"{timeout_seconds}s"])

    return connection


def record_history(text, read_only, status, row_count, duration_ms, error):
    try:
        with db.cursor() as cur:
            cur.execute("""
                INSERT INTO osm_app.query_history (sql, read_only, status, row_count, duration_ms, error)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, [text, read_only, status, row_count, duration_ms, error])
    except psycopg.Error:
        pass


@router.post("/run")
def run_query(body: RunRequest):
    text = clean_sql(body.sql)
    query_id = body.query_id or str(uuid.uuid4())
    keyword = first_keyword(text)
    started = time.perf_counter()
    notices = []

    connection = open_connection(body.read_only, body.timeout_seconds)
    connection.add_notice_handler(lambda diag: notices.append(diag.message_primary))

    with _running_lock:
        _running[query_id] = connection

    try:
        # Writes in a WITH clause cannot go through a cursor, so only stream them when read-only.
        streamable = keyword in STREAMABLE and (body.read_only or keyword != "with")

        if streamable:
            cur = connection.cursor(name="runner", row_factory=tuple_row)
            cur.execute(text)
        elif body.read_only:
            cur = connection.cursor(row_factory=tuple_row)
            cur.execute(text, binary=True)
        else:
            cur = connection.cursor(row_factory=tuple_row)
            cur.execute(text)
            # Several statements: show the last one that produced rows (or the last one).
            while cur.nextset():
                pass

        columns = []
        rows = []
        truncated = False

        if cur.description:
            columns = column_types(connection, cur.description)
            fetched = cur.fetchmany(body.max_rows + 1)
            truncated = len(fetched) > body.max_rows
            rows = [[to_json_value(value) for value in row] for row in fetched[: body.max_rows]]

        status_message = cur.statusmessage
        affected = cur.rowcount if not cur.description and cur.rowcount >= 0 else None
        cur.close()

        if body.read_only:
            connection.rollback()
        else:
            connection.commit()

        duration_ms = round((time.perf_counter() - started) * 1000)
        record_history(text, body.read_only, "ok", len(rows) if columns else affected, duration_ms, None)

        return {
            "status": "ok",
            "query_id": query_id,
            "statement": keyword.upper(),
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "truncated": truncated,
            "affected_rows": affected,
            "status_message": status_message,
            "notices": notices,
            "duration_ms": duration_ms,
        }

    except psycopg.Error as error:
        status = "cancelled" if isinstance(error, psycopg.errors.QueryCanceled) else "error"
        duration_ms = round((time.perf_counter() - started) * 1000)
        payload = error_payload(error, status)

        if status == "cancelled" and "statement timeout" in payload["message"]:
            payload["message"] = f"Query exceeded the {body.timeout_seconds}s timeout"

        record_history(text, body.read_only, status, None, duration_ms, payload["message"])

        return JSONResponse(
            {**payload, "query_id": query_id, "duration_ms": duration_ms, "notices": notices},
            status_code=400,
        )

    finally:
        with _running_lock:
            _running.pop(query_id, None)
        connection.close()


@router.post("/cancel/{query_id}")
def cancel_query(query_id: str):
    with _running_lock:
        connection = _running.get(query_id)

    if connection is None:
        return {"cancelled": False}

    connection.cancel_safe()
    return {"cancelled": True}


@router.post("/explain")
def explain_query(body: ExplainRequest):
    """Query plan. Always read-only and rolled back, so EXPLAIN ANALYZE cannot change data."""
    text = clean_sql(body.sql)
    options = "ANALYZE, BUFFERS, " if body.analyze else ""
    connection = open_connection(True, body.timeout_seconds)

    try:
        with connection.cursor(row_factory=tuple_row) as cur:
            cur.execute(f"EXPLAIN ({options}VERBOSE, COSTS) {text}", binary=True)
            plan = "\n".join(row[0] for row in cur.fetchall())
        return {"status": "ok", "plan": plan}
    except psycopg.Error as error:
        return JSONResponse(error_payload(error), status_code=400)
    finally:
        connection.rollback()
        connection.close()


@router.post("/export")
def export_query(body: ExportRequest):
    """Stream the complete result of a query as CSV (no row limit)."""
    text = clean_sql(body.sql)

    if first_keyword(text) not in STREAMABLE:
        raise HTTPException(400, "Only SELECT / WITH / VALUES / TABLE queries can be exported")

    connection = open_connection(body.read_only, body.timeout_seconds)
    cur = connection.cursor(name="export", row_factory=tuple_row)

    try:
        cur.execute(text)
    except psycopg.Error as error:
        connection.close()
        return JSONResponse(error_payload(error), status_code=400)

    def generate():
        buffer = io.StringIO()
        writer = csv.writer(buffer)

        try:
            writer.writerow([column.name for column in cur.description])

            while batch := cur.fetchmany(5000):
                writer.writerows([to_csv_value(value) for value in row] for row in batch)
                yield buffer.getvalue()
                buffer.seek(0)
                buffer.truncate()

            yield buffer.getvalue()
        finally:
            cur.close()
            connection.rollback()
            connection.close()

    return StreamingResponse(
        generate(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="query-result.csv"'},
    )


@router.get("/schema")
def schema():
    """Tables, views and their columns, for the schema browser and editor autocomplete."""
    rows = db.fetch_all("""
        SELECT n.nspname AS schema,
               c.relname AS name,
               CASE c.relkind WHEN 'v' THEN 'view' WHEN 'm' THEN 'materialized view'
                              WHEN 'f' THEN 'foreign table' ELSE 'table' END AS kind,
               GREATEST(c.reltuples, 0)::bigint AS estimated_rows,
               pg_total_relation_size(c.oid) AS size_bytes,
               json_agg(json_build_object(
                   'name', a.attname,
                   'type', format_type(a.atttypid, a.atttypmod)
               ) ORDER BY a.attnum) AS columns
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
        WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f')
          AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast', 'topology')
          AND n.nspname NOT LIKE 'pg_temp%%'
          AND NOT (n.nspname = 'public'
                   AND c.relname IN ('spatial_ref_sys', 'geometry_columns', 'geography_columns'))
        GROUP BY 1, 2, 3, 4, 5
        ORDER BY 1, 2
    """)

    return [{key: to_json_value(value) for key, value in row.items()} for row in rows]


@router.get("/history")
def history(limit: Annotated[int, Query(ge=1, le=500)] = 100):
    rows = db.fetch_all("""
        SELECT id, sql, read_only, status, row_count, duration_ms, error, ran_at
        FROM osm_app.query_history
        ORDER BY ran_at DESC
        LIMIT %s
    """, [limit])
    return [{key: to_json_value(value) for key, value in row.items()} for row in rows]


@router.delete("/history")
def clear_history():
    with db.cursor() as cur:
        cur.execute("DELETE FROM osm_app.query_history")
    return {"ok": True}


@router.get("/saved")
def list_saved():
    rows = db.fetch_all("""
        SELECT id, name, sql, created_at, updated_at
        FROM osm_app.saved_queries ORDER BY lower(name)
    """)
    return [{key: to_json_value(value) for key, value in row.items()} for row in rows]


@router.post("/saved")
def create_saved(body: SavedQuery):
    row = db.fetch_one("""
        INSERT INTO osm_app.saved_queries (name, sql) VALUES (%s, %s)
        RETURNING id, name, sql, created_at, updated_at
    """, [body.name.strip(), body.sql])
    return {key: to_json_value(value) for key, value in row.items()}


@router.put("/saved/{saved_id}")
def update_saved(saved_id: int, body: SavedQuery):
    row = db.fetch_one("""
        UPDATE osm_app.saved_queries SET name = %s, sql = %s, updated_at = now()
        WHERE id = %s
        RETURNING id, name, sql, created_at, updated_at
    """, [body.name.strip(), body.sql, saved_id])

    if row is None:
        raise HTTPException(404, "Saved query not found")

    return {key: to_json_value(value) for key, value in row.items()}


@router.delete("/saved/{saved_id}")
def delete_saved(saved_id: int):
    with db.cursor() as cur:
        cur.execute("DELETE FROM osm_app.saved_queries WHERE id = %s", [saved_id])
    return {"ok": True}
