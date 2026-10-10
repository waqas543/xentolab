"""Structured query DSL: validated filter trees compiled to parameterized SQL.

The frontend never sends SQL. Fields, operators and value types are checked against
the dataset's column metadata; identifiers come from the registry, and every value
is bound as a parameter. The same compiler targets DuckDB (Parquet) and PostgreSQL.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal, Union

from pydantic import BaseModel, Field, field_validator

from ..errors import ParameterError

Operator = Literal[
    "equals", "not_equals", "contains", "starts_with", "in", "not_in", "gt", "gte", "lt", "lte", "between",
    "is_null", "is_not_null", "has_key", "within_bbox", "within_radius",
]

OPERATORS_BY_TYPE: dict[str, list[str]] = {
    "text": ["equals", "not_equals", "contains", "starts_with", "in", "not_in", "is_null", "is_not_null"],
    "select": ["equals", "not_equals", "in", "not_in", "contains", "is_null", "is_not_null"],
    "number": ["equals", "not_equals", "gt", "gte", "lt", "lte", "between", "in", "not_in", "is_null", "is_not_null"],
    "boolean": ["equals", "is_null", "is_not_null"],
    "date": ["equals", "gt", "gte", "lt", "lte", "between", "is_null", "is_not_null"],
    "datetime": ["gt", "gte", "lt", "lte", "between", "is_null", "is_not_null"],
    "list": ["contains", "in", "not_in", "is_null", "is_not_null"],        # array column: element tests
    "map": ["has_key", "is_null", "is_not_null"],
    "json": ["contains", "is_null", "is_not_null"],
    "geo": ["within_bbox", "within_radius"],
}

MAX_CONDITIONS = 50
MAX_DEPTH = 4
MAX_IN_VALUES = 500


class Condition(BaseModel):
    field: str
    operator: Operator
    value: Any = None


class Group(BaseModel):
    operator: Literal["and", "or"] = "and"
    conditions: list[Union["Condition", "Group"]] = Field(default_factory=list)


class SortSpec(BaseModel):
    field: str
    direction: Literal["asc", "desc"] = "asc"


class QueryRequest(BaseModel):
    filters: Group | None = None
    search: str | None = None
    columns: list[str] | None = None
    sort: list[SortSpec] = Field(default_factory=list)
    page: int = Field(default=1, ge=1, le=1_000_000)
    page_size: int = Field(default=50, ge=1, le=500)

    @field_validator("search")
    @classmethod
    def _trim(cls, value):
        return value.strip() or None if value else None


Group.model_rebuild()


class Dialect:
    name = "postgres"
    placeholder = "%s"

    def list_contains(self, column: str) -> str:
        return f"%s = ANY({column})"

    def list_has_any(self, column: str, count: int) -> str:
        return f"{column} && ARRAY[{', '.join(['%s'] * count)}]::text[]"

    def map_has_key(self, column: str) -> str:
        return f"{column} ? %s"

    def text(self, column: str) -> str:
        return f"CAST({column} AS TEXT)"


class DuckDialect(Dialect):
    name = "duckdb"
    placeholder = "?"

    def list_contains(self, column: str) -> str:
        return f"list_contains({column}, ?)"

    def list_has_any(self, column: str, count: int) -> str:
        return f"list_has_any({column}, [{', '.join(['?'] * count)}])"

    def map_has_key(self, column: str) -> str:
        return f"map_contains({column}, ?)"

    def text(self, column: str) -> str:
        return f"CAST({column} AS VARCHAR)"


POSTGRES = Dialect()
DUCKDB = DuckDialect()


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _coerce(column, value, path: str):
    """Validate/convert one scalar for a column type."""
    kind = column.type
    if value is None:
        raise ParameterError(f"{path}: a value is required")
    try:
        if kind == "number":
            if isinstance(value, bool):
                raise ValueError
            return float(value) if column.decimal else int(value)
        if kind == "boolean":
            if isinstance(value, bool):
                return value
            if str(value).lower() in ("true", "yes", "1"):
                return True
            if str(value).lower() in ("false", "no", "0"):
                return False
            raise ValueError
        if kind == "date":
            return date.fromisoformat(str(value)[:10])
        if kind == "datetime":
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        text = str(value)
        if len(text) > 1000:
            raise ParameterError(f"{path}: value too long")
        return text
    except (TypeError, ValueError):
        raise ParameterError(f"{path}: {value!r} is not a valid {kind}") from None


class Compiler:
    def __init__(self, columns: dict, dialect: Dialect):
        self.columns = columns
        self.dialect = dialect
        self.params: list = []
        self.count = 0

    def _column(self, name: str, path: str):
        column = self.columns.get(name)
        if column is None or not column.filterable:
            raise ParameterError(f"{path}: unknown or non-filterable field {name!r}")
        return column

    def compile(self, node: Group | None, depth: int = 0, path: str = "filters") -> str:
        if node is None or not node.conditions:
            return "TRUE"
        if depth >= MAX_DEPTH:
            raise ParameterError(f"{path}: filters nested too deeply")
        parts = []
        for index, item in enumerate(node.conditions):
            item_path = f"{path}.conditions[{index}]"
            if isinstance(item, Group):
                parts.append(f"({self.compile(item, depth + 1, item_path)})")
            else:
                self.count += 1
                if self.count > MAX_CONDITIONS:
                    raise ParameterError(f"At most {MAX_CONDITIONS} conditions are allowed")
                parts.append(self.condition(item, item_path))
        return f" {node.operator.upper()} ".join(parts)

    def condition(self, cond: Condition, path: str) -> str:
        column = self._column(cond.field, path)
        if cond.operator not in OPERATORS_BY_TYPE[column.type]:
            raise ParameterError(f"{path}: operator {cond.operator!r} is not valid for {column.type} field "
                                 f"{cond.field!r}")
        sql = column.sql(self.dialect)
        ph = self.dialect.placeholder
        op = cond.operator
        value = cond.value

        if op == "is_null":
            if column.type == "list":
                return f"({sql} IS NULL OR {'len' if self.dialect is DUCKDB else 'cardinality'}({sql}) = 0)"
            return f"{sql} IS NULL"
        if op == "is_not_null":
            return f"{sql} IS NOT NULL"

        if column.type == "geo":
            lat, lon = column.geo
            if op == "within_bbox":
                if not isinstance(value, (list, tuple)) or len(value) != 4:
                    raise ParameterError(f"{path}: within_bbox needs [west, south, east, north]")
                west, south, east, north = (float(v) for v in value)
                self.params += [south, north, west, east]
                return f"{lat} BETWEEN {ph} AND {ph} AND {lon} BETWEEN {ph} AND {ph}"
            if not isinstance(value, dict) or not {"lat", "lon", "km"} <= set(value):
                raise ParameterError(f"{path}: within_radius needs {{lat, lon, km}}")
            clat, clon, km = float(value["lat"]), float(value["lon"]), float(value["km"])
            self.params += [clat, clat, clon, km]
            return (f"2 * 6371.0088 * asin(sqrt(power(sin(radians({lat} - {ph}) / 2), 2) + cos(radians({lat})) * "
                    f"cos(radians({ph})) * power(sin(radians({lon} - {ph}) / 2), 2))) <= {ph}")

        if column.type == "list":
            values = value if isinstance(value, list) else [value]
            values = [_coerce(column, v, path) for v in values][:MAX_IN_VALUES]
            if op == "contains":
                self.params.append(values[0])
                return self.dialect.list_contains(sql)
            self.params += values
            clause = self.dialect.list_has_any(sql, len(values))
            return clause if op == "in" else f"NOT coalesce({clause}, false)"

        if column.type == "map":
            self.params.append(_coerce(column, value, path))
            return self.dialect.map_has_key(sql)

        if op in ("in", "not_in"):
            if not isinstance(value, list) or not value:
                raise ParameterError(f"{path}: {op} needs a non-empty list")
            if len(value) > MAX_IN_VALUES:
                raise ParameterError(f"{path}: at most {MAX_IN_VALUES} values")
            self.params += [_coerce(column, v, path) for v in value]
            placeholders = ", ".join([ph] * len(value))
            return f"{sql} IN ({placeholders})" if op == "in" else f"({sql} IS NULL OR {sql} NOT IN ({placeholders}))"

        if op in ("contains", "starts_with"):
            text = str(_coerce(column, value, path))
            self.params.append(("%" if op == "contains" else "") + _escape_like(text) + "%")
            return f"{self.dialect.text(sql)} ILIKE {ph} ESCAPE '\\'"

        if op == "between":
            if not isinstance(value, list) or len(value) != 2:
                raise ParameterError(f"{path}: between needs [low, high]")
            self.params += [_coerce(column, value[0], path), _coerce(column, value[1], path)]
            return f"{sql} BETWEEN {ph} AND {ph}"

        symbol = {"equals": "=", "not_equals": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}[op]
        self.params.append(_coerce(column, value, path))
        if op == "not_equals":
            return f"({sql} IS NULL OR {sql} <> {ph})"
        return f"{sql} {symbol} {ph}"


def compile_filters(group: Group | None, columns: dict, dialect: Dialect) -> tuple[str, list]:
    compiler = Compiler(columns, dialect)
    return compiler.compile(group), compiler.params


def compile_search(search: str | None, columns: dict, dialect: Dialect) -> tuple[str, list]:
    if not search:
        return "TRUE", []
    searchable = [c for c in columns.values() if c.searchable]
    if not searchable:
        return "TRUE", []
    pattern = "%" + _escape_like(search) + "%"
    clauses = [f"{dialect.text(c.sql(dialect))} ILIKE {dialect.placeholder} ESCAPE '\\'" for c in searchable]
    return "(" + " OR ".join(clauses) + ")", [pattern] * len(searchable)


def partition_values(group: Group | None, field: str) -> list[str] | None:
    """Values a top-level AND filter pins `field` to (for partition pruning), else None."""
    if group is None or group.operator != "and":
        return None
    for cond in group.conditions:
        if isinstance(cond, Condition) and cond.field == field:
            if cond.operator == "equals" and cond.value is not None:
                return [str(cond.value)]
            if cond.operator == "in" and isinstance(cond.value, list):
                return [str(v) for v in cond.value]
    return None
