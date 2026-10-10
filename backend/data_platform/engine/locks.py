"""Scope locks: stages that write a shared, cross-source partition run one at a time per scope.

Entity resolution, Gold and serving for a country combine every source's Silver, so an
OSM run and an Overture run for the same country must not interleave there. Their
download/Bronze/Silver stages still run in parallel.
"""

from __future__ import annotations

import time
from contextlib import contextmanager

from .. import db
from ..observability import get_logger
from ..states import Stage

log = get_logger(__name__)

GOLD_SCOPE_STAGES = {Stage.ENTITY_RESOLUTION, Stage.GOLD_BUILD, Stage.GOLD_QUALITY, Stage.ANALYTICS_REFRESH}


def scope_key(params: dict) -> str:
    return f"gold:{params.get('country') or 'global'}"


@contextmanager
def gold_scope_lock(ctx, poll_seconds: float = 1.0):
    """Hold a PostgreSQL advisory lock for the stage; waiting stays cancellable and visible as progress."""
    key = scope_key(ctx.params)
    conn = db.connect("control", autocommit=True)
    try:
        waited = 0.0
        while not conn.execute("SELECT pg_try_advisory_lock(hashtext(%s)) AS ok", [key]).fetchone()["ok"]:
            if waited == 0:
                log.info("waiting for another run to release the Gold scope", scope=key)
            ctx.progress(0, None, "seconds", f"waiting for another run to finish {key} ({int(waited)} s)",
                         force=True)                              # raises RunCancelled if cancelled while waiting
            time.sleep(poll_seconds)
            waited += poll_seconds
        yield
    finally:
        conn.close()                                              # session end releases the lock
