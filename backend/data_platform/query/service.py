"""Executes validated explorer queries on DuckDB (Parquet layers) or PostgreSQL (serving)."""

from __future__ import annotations

import csv
import datetime
import decimal
import io
import json
import math
from collections.abc import Iterator

from .. import db, repository
from ..errors import ParameterError
from ..lake.reader import dataset_files, duck, parquet_source
from .dsl import Group, QueryRequest, compile_filters, compile_search, partition_values
from .registry import DatasetSpec

MAX_SAFE_INTEGER = 2**53 - 1
EXPORT_ROW_LIMIT = 1_000_000


def jsonable(value):
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int):
        return value if abs(value) <= MAX_SAFE_INTEGER else str(value)
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, decimal.Decimal):
        return float(value) if value.is_finite() else None
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "\\x" + bytes(value).hex()
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return str(value)


def _versions_for(spec: DatasetSpec, filters: Group | None) -> list[dict]:
    partition: dict = {}
    pinned: dict[str, list[str]] = {}
    for field, partition_column in spec.partition_filters.items():
        values = partition_values(filters, field)
        if values:
            pinned[partition_column] = values
    versions = repository.current_versions(spec.id, partition or None)
    for column, values in pinned.items():
        versions = [v for v in versions if str(v["partition"].get(column)) in values]
    return versions


class Source:
    """A FROM clause plus a way to run SQL against it."""

    def __init__(self, spec: DatasetSpec, filters: Group | None):
        self.spec = spec
        self.dialect = spec.dialect()
        if spec.engine == "parquet":
            files = dataset_files(spec.id, versions=_versions_for(spec, filters))
            self.from_sql = parquet_source(files) if files else None
        else:
            self.from_sql = spec.table

    def fetch(self, sql: str, params: list) -> tuple[list[str], list[tuple]]:
        if self.spec.engine == "parquet":
            con = duck()
            try:
                result = con.execute(sql, params)
                names = [d[0] for d in result.description]
                return names, result.fetchall()
            finally:
                con.close()
        with db.serving() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            names = [d.name for d in cur.description]
            return names, [tuple(row.values()) for row in cur.fetchall()]

    def stream(self, sql: str, params: list, batch: int = 5000) -> Iterator[tuple[list[str], list[tuple]]]:
        if self.spec.engine == "parquet":
            con = duck()
            try:
                result = con.execute(sql, params)
                names = [d[0] for d in result.description]
                while rows := result.fetchmany(batch):
                    yield names, rows
            finally:
                con.close()
            return
        with db.serving() as conn, conn.transaction(), conn.cursor(name="dp_export") as cur:
            cur.execute(sql, params)
            names = None
            while rows := cur.fetchmany(batch):
                names = names or [d.name for d in cur.description]
                yield names, [tuple(r.values()) for r in rows]


def _where(spec: DatasetSpec, request: QueryRequest) -> tuple[str, list]:
    columns = spec.column_map
    filter_sql, filter_params = compile_filters(request.filters, columns, spec.dialect())
    search_sql, search_params = compile_search(request.search, columns, spec.dialect())
    return f"({filter_sql}) AND ({search_sql})", filter_params + search_params


def _select_list(spec: DatasetSpec, requested: list[str] | None) -> tuple[str, list[str]]:
    columns = spec.column_map
    if requested:
        unknown = [c for c in requested if c not in columns]
        if unknown:
            raise ParameterError(f"Unknown columns: {', '.join(unknown)}")
        names = [c for c in requested if columns[c].type != "geo"]
    else:
        names = [c.name for c in spec.columns if c.visible and c.type != "geo"]
    if spec.primary_key and spec.primary_key not in names:
        names = [spec.primary_key] + names
    dialect = spec.dialect()
    return ", ".join(columns[n].select_sql(dialect) for n in names), names


def _order_by(spec: DatasetSpec, request: QueryRequest) -> str:
    columns = spec.column_map
    sorts = request.sort or [type("S", (), {"field": spec.default_sort[0], "direction": spec.default_sort[1]})()]
    parts = []
    for sort in sorts[:3]:
        column = columns.get(sort.field)
        if column is None or not column.sortable or column.type == "geo":
            raise ParameterError(f"Cannot sort by {sort.field!r}")
        parts.append(f"{column.sql(spec.dialect())} {'DESC' if sort.direction == 'desc' else 'ASC'} NULLS LAST")
    if spec.primary_key and spec.primary_key not in [s.field for s in sorts]:
        parts.append(spec.column_map[spec.primary_key].sql(spec.dialect()))
    return ", ".join(parts)


def run_query(spec: DatasetSpec, request: QueryRequest) -> dict:
    select, names = _select_list(spec, request.columns)
    source = Source(spec, request.filters)
    if source.from_sql is None:
        return {"columns": names, "rows": [], "total": 0, "page": request.page, "page_size": request.page_size}
    where, params = _where(spec, request)
    _, count_rows = source.fetch(f"SELECT count(*) FROM {source.from_sql} WHERE {where}", params)
    offset = (request.page - 1) * request.page_size
    ph = spec.dialect().placeholder
    _, rows = source.fetch(f"""
        SELECT {select} FROM {source.from_sql} WHERE {where}
        ORDER BY {_order_by(spec, request)} LIMIT {ph} OFFSET {ph}
    """, params + [request.page_size, offset])
    return {
        "columns": names,
        "rows": [[jsonable(v) for v in row] for row in rows],
        "total": int(count_rows[0][0]),
        "page": request.page,
        "page_size": request.page_size,
    }


def facet_values(spec: DatasetSpec, field: str, *, search: str | None, filters: Group | None,
                 limit: int = 50) -> list[dict]:
    column = spec.column_map.get(field)
    if column is None or not column.filterable or column.type in ("geo", "map", "json"):
        raise ParameterError(f"No value list for {field!r}")
    request = QueryRequest(filters=filters)
    source = Source(spec, filters)
    if source.from_sql is None:
        return []
    where, params = _where(spec, request)
    dialect = spec.dialect()
    ph = dialect.placeholder
    expression = column.sql(dialect)
    if column.type == "list":
        value_sql = f"unnest({expression})"
    else:
        value_sql = expression
    search_clause = ""
    if search:
        search_clause = f"WHERE {dialect.text('value')} ILIKE {ph}"
        params = params + [f"%{search}%"]
    _, rows = source.fetch(f"""
        SELECT value, count(*) AS n FROM (
            SELECT {value_sql} AS value FROM {source.from_sql} WHERE {where}
        ) v {search_clause}
        GROUP BY value ORDER BY n DESC, value LIMIT {ph}
    """, params + [limit])
    return [{"value": jsonable(value), "count": int(count)} for value, count in rows if value is not None]


def export_csv(spec: DatasetSpec, request: QueryRequest, limit: int = EXPORT_ROW_LIMIT) -> Iterator[str]:
    select, names = _select_list(spec, request.columns)
    source = Source(spec, request.filters)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(names)
    yield buffer.getvalue()
    if source.from_sql is None:
        return
    where, params = _where(spec, request)
    sql = (f"SELECT {select} FROM {source.from_sql} WHERE {where} ORDER BY {_order_by(spec, request)} "
           f"LIMIT {int(limit)}")
    for _, rows in source.stream(sql, params):
        buffer.seek(0)
        buffer.truncate()
        for row in rows:
            writer.writerow([json.dumps(v) if isinstance(v, (dict, list)) else jsonable(v) for v in row])
        yield buffer.getvalue()


def get_record(spec: DatasetSpec, key: str) -> dict | None:
    if not spec.primary_key:
        raise ParameterError(f"{spec.id} has no record view")
    column = spec.column_map[spec.primary_key]
    select = ", ".join(c.select_sql(spec.dialect()) for c in spec.columns if c.type != "geo")
    source = Source(spec, None)
    if source.from_sql is None:
        return None
    value = int(key) if column.type == "number" else key
    names, rows = source.fetch(f"SELECT {select} FROM {source.from_sql} WHERE {column.sql(spec.dialect())} = "
                               f"{spec.dialect().placeholder} LIMIT 1", [value])
    return {n: jsonable(v) for n, v in zip(names, rows[0])} if rows else None
