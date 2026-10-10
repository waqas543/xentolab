"""Turns schedules into runs. Runs inside worker processes; one leader at a time via advisory lock."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from croniter import croniter

from .. import db
from ..observability import get_logger
from ..sources.registry import find_pipeline
from ..states import RunStatus
from .service import start_run

log = get_logger(__name__)

PRESET_CRON = {"daily": "0 2 * * *", "weekly": "0 3 * * 1", "monthly": "0 4 1 * *"}


def next_run_time(schedule: dict, after: datetime) -> datetime | None:
    cadence = schedule["cadence"]
    if cadence == "manual":
        return None
    if cadence == "on_release":
        return after + timedelta(minutes=schedule["check_interval_minutes"])
    expression = schedule["cron"] if cadence == "cron" else PRESET_CRON[cadence]
    return croniter(expression, after).get_next(datetime)


def _has_open_run(concurrency_key: str) -> bool:
    with db.control() as conn:
        return conn.execute("""
            SELECT 1 FROM dp.pipeline_runs WHERE concurrency_key = %s AND NOT (status = ANY(%s)) LIMIT 1
        """, [concurrency_key, [str(s) for s in (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED,
                                                  RunStatus.PARTIALLY_COMPLETED)]]).fetchone() is not None


def _new_release_available(schedule: dict) -> bool:
    adapter, pipeline = find_pipeline(schedule["pipeline_definition_id"])
    params = adapter.validate_parameters(pipeline, schedule["parameters"])
    release = adapter.latest_release(params)
    if release is None:
        return False
    with db.control() as conn:
        known = conn.execute("""
            SELECT 1 FROM dp.dataset_artifacts
            WHERE source_id = %s AND status = 'available' AND (source_version = %s OR source_checksum = %s)
            LIMIT 1
        """, [adapter.source_id, release.source_version, release.checksum]).fetchone()
    return known is None


def tick(now: datetime | None = None) -> list[int]:
    """Enqueue due schedules. Returns created run ids."""
    now = now or datetime.now(timezone.utc)
    created = []
    with db.control() as conn, conn.transaction():
        if not conn.execute("SELECT pg_try_advisory_xact_lock(hashtext('dp_scheduler')) AS ok").fetchone()["ok"]:
            return []
        due = conn.execute("""
            SELECT * FROM dp.pipeline_schedules
            WHERE enabled AND cadence <> 'manual' AND (next_run_at IS NULL OR next_run_at <= %s)
            -- NO KEY UPDATE: exclusive among schedulers, but compatible with the FK check of the
            -- pipeline_runs row that start_run() inserts on another connection.
            FOR NO KEY UPDATE SKIP LOCKED
        """, [now]).fetchall()
        for schedule in due:
            next_at = next_run_time(schedule, now)
            run_id = None
            try:
                adapter, pipeline = find_pipeline(schedule["pipeline_definition_id"])
                params = adapter.validate_parameters(pipeline, schedule["parameters"])
                if _has_open_run(adapter.concurrency_key(params)):
                    log.info("schedule skipped: a run is already queued or running", schedule=schedule["id"])
                elif schedule["cadence"] != "on_release" or _new_release_available(schedule):
                    run = start_run(schedule["pipeline_definition_id"], schedule["parameters"],
                                    trigger_type="schedule", triggered_by=f"schedule:{schedule['name']}",
                                    schedule_id=schedule["id"])
                    run_id = run["id"]
                    created.append(run_id)
            except Exception:
                log.exception("schedule failed to enqueue", schedule=schedule["id"])
            conn.execute("""
                UPDATE dp.pipeline_schedules SET next_run_at = %s, updated_at = now(),
                    last_enqueued_at = CASE WHEN %s::bigint IS NULL THEN last_enqueued_at ELSE now() END,
                    last_run_id = COALESCE(%s, last_run_id)
                WHERE id = %s
            """, [next_at, run_id, run_id, schedule["id"]])
    return created


def upsert_schedule(definition_id: str, name: str, parameters: dict, cadence: str, cron: str | None = None,
                    enabled: bool = True, check_interval_minutes: int = 360) -> dict:
    adapter, pipeline = find_pipeline(definition_id)
    adapter.validate_parameters(pipeline, parameters)
    if cadence == "cron":
        if not cron or not croniter.is_valid(cron):
            raise ValueError("A valid cron expression is required for cadence=cron")
    schedule = {"cadence": cadence, "cron": cron, "check_interval_minutes": check_interval_minutes}
    next_at = next_run_time(schedule, datetime.now(timezone.utc))
    with db.control() as conn:
        return conn.execute("""
            INSERT INTO dp.pipeline_schedules (pipeline_definition_id, name, parameters, cadence, cron,
                check_interval_minutes, enabled, next_run_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING *
        """, [definition_id, name, db.Jsonb(parameters), cadence, cron, check_interval_minutes, enabled,
              next_at]).fetchone()


def list_schedules() -> list[dict]:
    with db.control() as conn:
        return conn.execute("SELECT * FROM dp.pipeline_schedules ORDER BY id").fetchall()
