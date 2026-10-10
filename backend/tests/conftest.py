"""Test setup: an isolated database and data directory for the whole session.

Integration tests need a local PostgreSQL with PostGIS. They use TEST_DATABASE_NAME
(default ds_platform_test), which is created on demand and reset every session.
"""

import os
import sys
from pathlib import Path

import psycopg
import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

TEST_DB = os.environ.get("TEST_DATABASE_NAME", "ds_platform_test")
ADMIN_DSN = os.environ.get("TEST_ADMIN_DSN", "dbname=postgres")


@pytest.fixture(scope="session")
def data_dir(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("data")


@pytest.fixture(scope="session", autouse=True)
def platform_env(data_dir):
    os.environ["DATABASE_URL"] = f"dbname={TEST_DB}"
    os.environ.pop("CONTROL_DATABASE_URL", None)
    os.environ.pop("SERVING_DATABASE_URL", None)
    os.environ["DATA_PLATFORM_DATA_DIR"] = str(data_dir)
    os.environ["DATA_PLATFORM_MIN_FREE_DISK_MB"] = "1"
    os.environ["DATA_PLATFORM_AUTH_MODE"] = "dev"
    from data_platform.config import get_settings

    get_settings.cache_clear()
    yield
    from data_platform import db

    db.close_pools()


@pytest.fixture(scope="session")
def database(platform_env):
    """A freshly migrated test database."""
    try:
        with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
            exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", [TEST_DB]).fetchone()
            if not exists:
                conn.execute(f'CREATE DATABASE "{TEST_DB}"')
    except psycopg.OperationalError as error:
        pytest.skip(f"PostgreSQL not available: {error}")

    with psycopg.connect(f"dbname={TEST_DB}", autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS dp CASCADE; DROP SCHEMA IF EXISTS gold CASCADE; "
                     "DROP SCHEMA IF EXISTS er CASCADE; DROP TABLE IF EXISTS public.dp_schema_migrations")
    from data_platform.migrate import migrate_all

    migrate_all()
    return TEST_DB
