"""Reusable data quality checks.

Each check compiles to one DuckDB query over a dataset version and reports
records_checked / records_failed. Severity "error" fails the stage when the failure
percentage exceeds the threshold; "warn" only records the result.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone


def _ident(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise ValueError(f"Invalid column name {name!r}")
    return f'"{name}"'


@dataclass(frozen=True)
class CheckOutcome:
    records_checked: int
    records_failed: int
    details: dict = field(default_factory=dict)

    @property
    def failure_percentage(self) -> float:
        return 0.0 if not self.records_checked else 100.0 * self.records_failed / self.records_checked


@dataclass(frozen=True)
class QualityCheck:
    name: str
    severity: str = "error"                 # error | warn
    threshold_pct: float = 0.0              # allowed failure percentage
    where: str | None = None                # optional row filter (trusted, adapter-defined SQL)

    check_type = "custom"

    def with_overrides(self, overrides: dict) -> "QualityCheck":
        allowed = {k: v for k, v in overrides.items() if k in ("severity", "threshold_pct")}
        return replace(self, **allowed)

    def _scope(self) -> str:
        return f"WHERE {self.where}" if self.where else ""

    def evaluate(self, con, source: str, context: dict) -> CheckOutcome:
        raise NotImplementedError

    def _count(self, con, source: str, failing_predicate: str) -> CheckOutcome:
        scope = self._scope()
        row = con.execute(f"""
            SELECT count(*), count(*) FILTER (WHERE {failing_predicate}) FROM {source} {scope}
        """).fetchone()
        return CheckOutcome(int(row[0]), int(row[1]))


@dataclass(frozen=True)
class NotNullCheck(QualityCheck):
    column: str = ""
    check_type = "not_null"

    def evaluate(self, con, source, context):
        col = _ident(self.column)
        return self._count(con, source, f"{col} IS NULL OR CAST({col} AS VARCHAR) = ''")


@dataclass(frozen=True)
class UniqueCheck(QualityCheck):
    columns: tuple[str, ...] = ()
    check_type = "unique"

    def evaluate(self, con, source, context):
        cols = ", ".join(_ident(c) for c in self.columns)
        row = con.execute(f"""
            SELECT count(*), count(*) - count(DISTINCT ({cols})) FROM {source} {self._scope()}
        """).fetchone()
        return CheckOutcome(int(row[0]), int(row[1]), {"columns": list(self.columns)})


@dataclass(frozen=True)
class RangeCheck(QualityCheck):
    column: str = ""
    min_value: float | None = None
    max_value: float | None = None
    check_type = "range"

    def evaluate(self, con, source, context):
        col = _ident(self.column)
        conditions = []
        if self.min_value is not None:
            conditions.append(f"{col} < {float(self.min_value)}")
        if self.max_value is not None:
            conditions.append(f"{col} > {float(self.max_value)}")
        outcome = self._count(con, source, f"{col} IS NOT NULL AND ({' OR '.join(conditions) or 'false'})")
        return replace(outcome, details={"min": self.min_value, "max": self.max_value})


@dataclass(frozen=True)
class RegexCheck(QualityCheck):
    column: str = ""
    pattern: str = ""
    check_type = "regex"

    def evaluate(self, con, source, context):
        col = _ident(self.column)
        pattern = self.pattern.replace("'", "''")
        outcome = self._count(con, source, f"{col} IS NOT NULL AND NOT regexp_full_match(CAST({col} AS VARCHAR), '{pattern}')")
        return replace(outcome, details={"pattern": self.pattern})


@dataclass(frozen=True)
class CoordinateCheck(QualityCheck):
    latitude: str = "latitude"
    longitude: str = "longitude"
    check_type = "coordinates"

    def evaluate(self, con, source, context):
        lat, lon = _ident(self.latitude), _ident(self.longitude)
        return self._count(con, source, f"""
            {lat} IS NULL OR {lon} IS NULL OR {lat} NOT BETWEEN -90 AND 90 OR {lon} NOT BETWEEN -180 AND 180
            OR ({lat} = 0 AND {lon} = 0)
        """)


@dataclass(frozen=True)
class DomainCheck(QualityCheck):
    column: str = "normalized_domain"
    check_type = "domain"
    PATTERN = r"([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{1,62}"

    def evaluate(self, con, source, context):
        col = _ident(self.column)
        return self._count(con, source, f"{col} IS NOT NULL AND NOT regexp_full_match({col}, '{self.PATTERN}')")


@dataclass(frozen=True)
class PhoneCheck(QualityCheck):
    """Fails rows that have a raw phone but no valid normalized (E.164) phone."""

    raw_column: str = "phone"
    normalized_column: str = "normalized_phone"
    check_type = "phone"

    def evaluate(self, con, source, context):
        raw, normalized = _ident(self.raw_column), _ident(self.normalized_column)
        return self._count(con, source, f"{raw} IS NOT NULL AND {normalized} IS NULL")


@dataclass(frozen=True)
class RecordCountCheck(QualityCheck):
    """Row count within bounds, and not shrinking/growing more than max_change_pct vs the previous version."""

    min_rows: int = 1
    max_rows: int | None = None
    max_change_pct: float | None = None
    check_type = "record_count"

    def evaluate(self, con, source, context):
        rows = int(con.execute(f"SELECT count(*) FROM {source} {self._scope()}").fetchone()[0])
        previous = context.get("previous_row_count")
        problems = []
        if rows < self.min_rows:
            problems.append(f"{rows} rows < minimum {self.min_rows}")
        if self.max_rows is not None and rows > self.max_rows:
            problems.append(f"{rows} rows > maximum {self.max_rows}")
        change = None
        note = None
        if self.max_change_pct is not None and previous:
            change = 100.0 * (rows - previous) / previous
            if context.get("composition_changed"):
                # A source was added or removed: a big change is expected, not an anomaly.
                note = (f"not compared: inputs changed from {context.get('previous_sources')} "
                        f"to {context.get('sources')}")
            elif abs(change) > self.max_change_pct:
                problems.append(f"row count changed {change:+.1f}% vs previous version ({previous})")
        return CheckOutcome(1, 1 if problems else 0, {"rows": rows, "previous_rows": previous, "change_pct": change,
                                                      "problems": problems, "note": note})


@dataclass(frozen=True)
class SchemaCheck(QualityCheck):
    """Required columns exist with compatible types. Extra columns are allowed (schema evolution)."""

    required: tuple[tuple[str, str], ...] = ()   # (column, type prefix[|alternative]) e.g. ("geometry", "BLOB|GEOMETRY")
    check_type = "schema"

    def evaluate(self, con, source, context):
        actual = {row[0]: row[1] for row in con.execute(f"DESCRIBE SELECT * FROM {source}").fetchall()}
        problems = []
        for column, type_prefix in self.required:
            if column not in actual:
                problems.append(f"missing column {column}")
            elif not any(str(actual[column]).upper().startswith(t.upper()) for t in type_prefix.split("|")):
                problems.append(f"{column} is {actual[column]}, expected {type_prefix}")
        return CheckOutcome(len(self.required), len(problems), {"problems": problems, "columns": actual})


@dataclass(frozen=True)
class FreshnessCheck(QualityCheck):
    """The newest value of a timestamp column is at most max_age_days old."""

    column: str = ""
    max_age_days: float = 30
    check_type = "freshness"

    def evaluate(self, con, source, context):
        newest = con.execute(f"SELECT max({_ident(self.column)}) FROM {source} {self._scope()}").fetchone()[0]
        if newest is None:
            return CheckOutcome(1, 1, {"newest": None})
        if newest.tzinfo is None:
            newest = newest.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - newest).total_seconds() / 86400
        return CheckOutcome(1, int(age_days > self.max_age_days),
                            {"newest": newest.isoformat(), "age_days": round(age_days, 2)})


@dataclass(frozen=True)
class DuplicateRateCheck(QualityCheck):
    """Share of rows whose key columns duplicate another row."""

    columns: tuple[str, ...] = ()
    check_type = "duplicate_rate"

    def evaluate(self, con, source, context):
        cols = ", ".join(_ident(c) for c in self.columns)
        not_null = " AND ".join(f"{_ident(c)} IS NOT NULL" for c in self.columns)
        scope = f"WHERE {not_null}" + (f" AND ({self.where})" if self.where else "")
        row = con.execute(f"""
            SELECT coalesce(sum(n), 0), coalesce(sum(n) FILTER (WHERE n > 1), 0)
            FROM (SELECT count(*) AS n FROM {source} {scope} GROUP BY {cols})
        """).fetchone()
        return CheckOutcome(int(row[0]), int(row[1]), {"columns": list(self.columns)})
