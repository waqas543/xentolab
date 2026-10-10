"""Connections to the control-plane and serving databases (they may be the same server)."""

import threading
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb, set_json_dumps
from psycopg_pool import ConnectionPool

from .config import get_settings

_pools: dict[str, ConnectionPool] = {}
_lock = threading.Lock()


def _json_dumps(value):
    import json

    return json.dumps(value, default=str)


set_json_dumps(_json_dumps)


def _pool(kind: str) -> ConnectionPool:
    with _lock:
        if kind not in _pools:
            settings = get_settings()
            dsn = settings.control_dsn if kind == "control" else settings.serving_dsn
            pool = ConnectionPool(
                dsn,
                min_size=1,
                max_size=8,
                kwargs={"row_factory": dict_row, "autocommit": True},
                open=True,
                name=f"dp-{kind}",
            )
            _pools[kind] = pool
        return _pools[kind]


@contextmanager
def control():
    with _pool("control").connection() as conn:
        yield conn


@contextmanager
def serving():
    with _pool("serving").connection() as conn:
        yield conn


def connect(kind: str = "control", **kwargs) -> psycopg.Connection:
    settings = get_settings()
    dsn = settings.control_dsn if kind == "control" else settings.serving_dsn
    return psycopg.connect(dsn, row_factory=dict_row, **kwargs)


def close_pools() -> None:
    with _lock:
        for pool in _pools.values():
            pool.close()
        _pools.clear()


__all__ = ["Jsonb", "control", "serving", "connect", "close_pools"]
