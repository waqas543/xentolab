"""Minimal, ordered SQL migrations for the control and serving databases.

Files live in migrations/<target>/NNNN_name.sql and are applied once each, in order,
inside a transaction, under an advisory lock (safe with several processes starting).
"""

from pathlib import Path

import psycopg

from .config import get_settings
from .observability import get_logger

log = get_logger(__name__)
MIGRATIONS_DIR = Path(__file__).parent / "migrations"
TARGETS = ("control", "serving")


def pending(conn: psycopg.Connection, target: str) -> list[Path]:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS public.dp_schema_migrations (
            target TEXT NOT NULL, version TEXT NOT NULL,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (target, version))
    """)
    applied = {row[0] for row in conn.execute(
        "SELECT version FROM public.dp_schema_migrations WHERE target = %s", [target]).fetchall()}
    files = sorted((MIGRATIONS_DIR / target).glob("*.sql"))
    return [f for f in files if f.stem not in applied]


def migrate(target: str, dsn: str | None = None) -> list[str]:
    settings = get_settings()
    dsn = dsn or (settings.control_dsn if target == "control" else settings.serving_dsn)
    applied = []
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(hashtext('dp_migrations'))")
        try:
            for path in pending(conn, target):
                with conn.transaction():
                    conn.execute(path.read_text())
                    conn.execute("INSERT INTO public.dp_schema_migrations (target, version) VALUES (%s, %s)",
                                 [target, path.stem])
                log.info("applied migration", target=target, version=path.stem)
                applied.append(path.stem)
        finally:
            conn.execute("SELECT pg_advisory_unlock(hashtext('dp_migrations'))")
    return applied


def migrate_all() -> dict[str, list[str]]:
    return {target: migrate(target) for target in TARGETS}
