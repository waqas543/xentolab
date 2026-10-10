import os
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

DSN = os.environ.get("DATABASE_URL", "dbname=ds_companies")

pool = ConnectionPool(
    DSN,
    min_size=1,
    max_size=10,
    open=False,
    kwargs={"row_factory": dict_row, "autocommit": True},
)


@contextmanager
def cursor():
    with pool.connection() as connection:
        with connection.cursor() as cur:
            yield cur


def fetch_all(sql, params=None):
    with cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def fetch_one(sql, params=None):
    with cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def connect(**kwargs):
    """A dedicated (unpooled) connection, used by the query runner."""
    return psycopg.connect(DSN, **kwargs)
