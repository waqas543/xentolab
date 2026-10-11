"""Connection pool for the app database (DATABASE_URL), opened on first use."""

from __future__ import annotations

import os
import threading

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

_pool: ConnectionPool | None = None
_lock = threading.Lock()


def pool() -> ConnectionPool:
    global _pool
    with _lock:
        if _pool is None:
            _pool = ConnectionPool(os.environ.get("DATABASE_URL", "dbname=ds_companies"), name="xento-api",
                                   min_size=1, max_size=int(os.environ.get("DB_POOL_SIZE", "10")), open=True,
                                   kwargs={"row_factory": dict_row, "autocommit": True})
        return _pool


def rows(sql: str, params: dict | None = None) -> list[dict]:
    with pool().connection() as conn:
        return conn.execute(sql, params or {}).fetchall()


def one(sql: str, params: dict | None = None) -> dict | None:
    found = rows(sql, params)
    return found[0] if found else None


def close() -> None:
    global _pool
    with _lock:
        if _pool is not None:
            _pool.close()
            _pool = None
