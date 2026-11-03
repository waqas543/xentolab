"""Resumable job state in a per-job SQLite file (output/state/<job>.sqlite).

- `items`: one row per unit of work (a company key, or `row:<n>` for skipped rows) with its final status.
  Items in DONE_STATUSES are never reprocessed; failed ones are retried on the next run.
- `seen`: de-duplication keys of records already written (e.g. company key + result URL).
- `runs`: one row per run with its stats, for `stats` and `resume`.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import orjson

from app.core.models import RecordStatus
from app.core.utils import utcnow

DONE_STATUSES = (RecordStatus.SUCCESS, RecordStatus.NO_RESULTS, RecordStatus.PARTIAL, RecordStatus.SKIPPED)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    job_id TEXT PRIMARY KEY, job_name TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
    status TEXT NOT NULL, stats TEXT
);
CREATE TABLE IF NOT EXISTS items (
    item_key TEXT PRIMARY KEY, status TEXT NOT NULL, job_id TEXT NOT NULL, row_number INTEGER,
    attempts INTEGER NOT NULL DEFAULT 1, last_error TEXT, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS seen (
    dedup_key TEXT PRIMARY KEY, record_type TEXT NOT NULL, job_id TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS items_status ON items(status);
"""


class StateStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._db = sqlite3.connect(path)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.executescript(_SCHEMA)

    # ------------------------------------------------------------ runs

    def start_run(self, job_id: str, job_name: str) -> None:
        with self._db:
            self._db.execute(
                "INSERT INTO runs (job_id, job_name, started_at, status) VALUES (?, ?, ?, 'running') "
                "ON CONFLICT(job_id) DO UPDATE SET status = 'running', finished_at = NULL",
                (job_id, job_name, utcnow().isoformat()))

    def finish_run(self, job_id: str, status: str, stats: dict[str, Any]) -> None:
        with self._db:
            self._db.execute("UPDATE runs SET finished_at = ?, status = ?, stats = ? WHERE job_id = ?",
                             (utcnow().isoformat(), status, orjson.dumps(stats).decode(), job_id))

    def latest_run(self) -> dict[str, Any] | None:
        row = self._db.execute("SELECT job_id, job_name, started_at, finished_at, status, stats FROM runs "
                               "ORDER BY started_at DESC LIMIT 1").fetchone()
        return _run(row) if row else None

    def runs(self, limit: int = 10) -> list[dict[str, Any]]:
        rows = self._db.execute("SELECT job_id, job_name, started_at, finished_at, status, stats FROM runs "
                                "ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
        return [_run(row) for row in rows]

    # ------------------------------------------------------------ items

    def done_keys(self) -> set[str]:
        marks = ",".join("?" * len(DONE_STATUSES))
        rows = self._db.execute(f"SELECT item_key FROM items WHERE status IN ({marks})",
                                [str(s) for s in DONE_STATUSES])
        return {row[0] for row in rows}

    def unseen(self, keys: Iterable[str]) -> set[str]:
        keys = set(keys)
        if not keys:
            return set()
        found: set[str] = set()
        ordered = list(keys)
        for start in range(0, len(ordered), 500):
            chunk = ordered[start:start + 500]
            marks = ",".join("?" * len(chunk))
            found.update(row[0] for row in self._db.execute(
                f"SELECT dedup_key FROM seen WHERE dedup_key IN ({marks})", chunk))
        return keys - found

    def commit_item(self, item_key: str, status: RecordStatus, job_id: str, *, row_number: int | None = None,
                    error: str | None = None, seen: Iterable[tuple[str, str]] = ()) -> None:
        """Record an item's outcome and the dedup keys it wrote, atomically (call after records are flushed)."""
        now = utcnow().isoformat()
        with self._db:
            self._db.execute(
                "INSERT INTO items (item_key, status, job_id, row_number, last_error, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(item_key) DO UPDATE SET status = excluded.status, "
                "job_id = excluded.job_id, attempts = items.attempts + 1, last_error = excluded.last_error, "
                "updated_at = excluded.updated_at",
                (item_key, str(status), job_id, row_number, error, now))
            self._db.executemany("INSERT OR IGNORE INTO seen (dedup_key, record_type, job_id) VALUES (?, ?, ?)",
                                 [(key, record_type, job_id) for record_type, key in seen])

    # ------------------------------------------------------------ reporting

    def status_counts(self) -> dict[str, int]:
        return dict(self._db.execute("SELECT status, COUNT(*) FROM items GROUP BY status").fetchall())

    def top_errors(self, limit: int = 5) -> list[tuple[str, int]]:
        return self._db.execute(
            "SELECT last_error, COUNT(*) AS n FROM items WHERE status = 'failed' AND last_error IS NOT NULL "
            "GROUP BY last_error ORDER BY n DESC LIMIT ?", (limit,)).fetchall()

    def reset(self) -> None:
        with self._db:
            for table in ("items", "seen", "runs"):
                self._db.execute(f"DELETE FROM {table}")

    def close(self) -> None:
        self._db.close()


def _run(row: tuple[Any, ...]) -> dict[str, Any]:
    job_id, job_name, started_at, finished_at, status, stats = row
    return {"job_id": job_id, "job_name": job_name, "started_at": started_at, "finished_at": finished_at,
            "status": status, "stats": orjson.loads(stats) if stats else {}}
