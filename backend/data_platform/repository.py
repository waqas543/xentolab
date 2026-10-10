"""Control-plane data access. The only module that writes run/stage state.

Status changes go through guarded UPDATEs (`WHERE status = ANY(allowed)`), so the
state machine holds even with several workers and API processes racing.
"""

import traceback as tb
from datetime import datetime, timezone

import psycopg

from . import db
from .db import Jsonb
from .errors import InvalidTransition, is_retryable
from .observability import redact
from .states import (
    EXECUTING_RUN_STATUSES,
    STAGE_RUN_STATUS,
    TERMINAL_RUN_STATUSES,
    RunStatus,
    Stage,
    StageStatus,
    allowed_run_sources,
    allowed_stage_sources,
    check_run_transition,
)


def now() -> datetime:
    return datetime.now(timezone.utc)


def partition_key(partition: dict) -> str:
    return "/".join(f"{k}={partition[k]}" for k in sorted(partition)) or "_"


# ---------------------------------------------------------------- sources & definitions

def upsert_source(source_id: str, name: str, description: str, homepage: str | None, license: str | None,
                  capabilities: dict) -> None:
    with db.control() as conn:
        conn.execute("""
            INSERT INTO dp.data_sources (id, name, description, homepage, license, capabilities)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, description = EXCLUDED.description,
                homepage = EXCLUDED.homepage, license = EXCLUDED.license,
                capabilities = EXCLUDED.capabilities, updated_at = now()
        """, [source_id, name, description, homepage, license, Jsonb(capabilities)])


def upsert_pipeline_definition(definition_id: str, source_id: str, name: str, description: str,
                               parameter_schema: dict, default_parameters: dict, stages: list[str]) -> None:
    # `enabled` and `quality_overrides` are operator settings and survive re-syncs.
    with db.control() as conn:
        conn.execute("""
            INSERT INTO dp.pipeline_definitions
                (id, source_id, name, description, parameter_schema, default_parameters, stages)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET source_id = EXCLUDED.source_id, name = EXCLUDED.name,
                description = EXCLUDED.description, parameter_schema = EXCLUDED.parameter_schema,
                default_parameters = EXCLUDED.default_parameters, stages = EXCLUDED.stages, updated_at = now()
        """, [definition_id, source_id, name, description, Jsonb(parameter_schema), Jsonb(default_parameters),
              stages])


def get_definition(definition_id: str) -> dict | None:
    with db.control() as conn:
        return conn.execute("SELECT * FROM dp.pipeline_definitions WHERE id = %s", [definition_id]).fetchone()


def list_definitions(source_id: str | None = None) -> list[dict]:
    with db.control() as conn:
        return conn.execute("""
            SELECT * FROM dp.pipeline_definitions WHERE %(s)s::text IS NULL OR source_id = %(s)s ORDER BY id
        """, {"s": source_id}).fetchall()


def update_definition_settings(definition_id: str, enabled: bool | None, quality_overrides: dict | None) -> dict | None:
    with db.control() as conn:
        return conn.execute("""
            UPDATE dp.pipeline_definitions
            SET enabled = COALESCE(%s, enabled), quality_overrides = COALESCE(%s, quality_overrides), updated_at = now()
            WHERE id = %s RETURNING *
        """, [enabled, Jsonb(quality_overrides) if quality_overrides is not None else None, definition_id]).fetchone()


def set_source_enabled(source_id: str, enabled: bool) -> dict | None:
    with db.control() as conn:
        return conn.execute("UPDATE dp.data_sources SET enabled = %s, updated_at = now() WHERE id = %s RETURNING *",
                            [enabled, source_id]).fetchone()


def get_source(source_id: str) -> dict | None:
    with db.control() as conn:
        return conn.execute("SELECT * FROM dp.data_sources WHERE id = %s", [source_id]).fetchone()


# ---------------------------------------------------------------- runs

def create_run(*, definition_id: str, source_id: str, parameters: dict, trigger_type: str,
               triggered_by: str | None, concurrency_key: str, country: str | None, dataset: str | None,
               stages: list[tuple[Stage, int, str | None]], retry_of_run_id: int | None = None,
               schedule_id: int | None = None, versions: dict | None = None) -> dict:
    """Create a run and its stage rows, and enqueue it — one transaction.

    `stages`: (stage, max_attempts, skip_reason). Stages with a skip reason start SKIPPED.
    """
    with db.control() as conn, conn.transaction():
        run = conn.execute("""
            INSERT INTO dp.pipeline_runs (pipeline_definition_id, source_id, status, trigger_type, triggered_by,
                parameters, concurrency_key, country, dataset, retry_of_run_id, schedule_id, versions)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *
        """, [definition_id, source_id, RunStatus.PENDING, trigger_type, triggered_by, Jsonb(redact(parameters)),
              concurrency_key, country, dataset, retry_of_run_id, schedule_id, Jsonb(versions or {})]).fetchone()

        with conn.cursor() as cur:
            cur.executemany("""
                INSERT INTO dp.pipeline_stage_runs (run_id, stage, position, status, max_attempts, skip_reason,
                    finished_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, [(run["id"], str(stage), position, StageStatus.SKIPPED if skip else StageStatus.PENDING,
                   max_attempts, skip, now() if skip else None)
                  for position, (stage, max_attempts, skip) in enumerate(stages)])

        check_run_transition(RunStatus.PENDING, RunStatus.QUEUED)
        return conn.execute("""
            UPDATE dp.pipeline_runs SET status = %s, queued_at = now(), updated_at = now()
            WHERE id = %s RETURNING *
        """, [RunStatus.QUEUED, run["id"]]).fetchone()


def claim_next_run(worker_id: str) -> dict | None:
    """Atomically take the oldest QUEUED run whose concurrency key is free."""
    with db.control() as conn:
        for _ in range(5):
            try:
                with conn.transaction():
                    candidate = conn.execute("""
                        SELECT r.id, (
                            SELECT s.stage FROM dp.pipeline_stage_runs s
                            WHERE s.run_id = r.id AND s.status = %(pending)s ORDER BY s.position LIMIT 1
                        ) AS first_stage
                        FROM dp.pipeline_runs r
                        WHERE r.status = %(queued)s
                          AND NOT EXISTS (
                              SELECT 1 FROM dp.pipeline_runs a
                              WHERE a.concurrency_key = r.concurrency_key AND a.status = ANY(%(executing)s))
                        ORDER BY r.created_at
                        LIMIT 1
                        FOR UPDATE OF r SKIP LOCKED
                    """, {"pending": StageStatus.PENDING, "queued": RunStatus.QUEUED,
                          "executing": [str(s) for s in EXECUTING_RUN_STATUSES]}).fetchone()
                    if candidate is None:
                        return None
                    first = Stage(candidate["first_stage"]) if candidate["first_stage"] else Stage.ANALYTICS_REFRESH
                    return conn.execute("""
                        UPDATE dp.pipeline_runs
                        SET status = %s, worker_id = %s, heartbeat_at = now(), started_at = COALESCE(started_at, now()),
                            updated_at = now()
                        WHERE id = %s AND status = %s RETURNING *
                    """, [STAGE_RUN_STATUS[first], worker_id, candidate["id"], RunStatus.QUEUED]).fetchone()
            except psycopg.errors.UniqueViolation:
                continue  # another worker started a run with the same concurrency key; look again
        return None


def set_run_status(run_id: int, target: RunStatus, **fields) -> dict:
    allowed = allowed_run_sources(target)
    assignments = ", ".join(f"{name} = %({name})s" for name in fields)
    values = {name: (Jsonb(v) if isinstance(v, (dict, list)) else v) for name, v in fields.items()}
    if target in TERMINAL_RUN_STATUSES:
        assignments = (assignments + ", " if assignments else "") + "finished_at = now()"
    with db.control() as conn:
        row = conn.execute(f"""
            UPDATE dp.pipeline_runs SET status = %(target)s, updated_at = now()
                {", " + assignments if assignments else ""}
            WHERE id = %(id)s AND status = ANY(%(allowed)s) RETURNING *
        """, {**values, "target": target, "id": run_id, "allowed": allowed}).fetchone()
    if row is None:
        current = get_run(run_id)
        raise InvalidTransition(f"Run {run_id} cannot move from {current and current['status']} to {target}")
    return row


def update_run(run_id: int, **fields) -> None:
    """Non-status fields (counters, current stage, outputs)."""
    if not fields:
        return
    assignments = ", ".join(f"{name} = %({name})s" for name in fields)
    values = {name: (Jsonb(v) if isinstance(v, (dict, list)) else v) for name, v in fields.items()}
    with db.control() as conn:
        conn.execute(f"UPDATE dp.pipeline_runs SET {assignments}, updated_at = now() WHERE id = %(id)s",
                     {**values, "id": run_id})


def increment_run(run_id: int, **increments: int) -> None:
    assignments = ", ".join(f"{name} = {name} + %({name})s" for name in increments)
    with db.control() as conn:
        conn.execute(f"UPDATE dp.pipeline_runs SET {assignments}, updated_at = now() WHERE id = %(id)s",
                     {**increments, "id": run_id})


def merge_run_outputs(run_id: int, outputs: dict) -> None:
    with db.control() as conn:
        conn.execute("UPDATE dp.pipeline_runs SET outputs = outputs || %s, updated_at = now() WHERE id = %s",
                     [Jsonb(outputs), run_id])


def heartbeat(run_id: int, worker_id: str) -> None:
    with db.control() as conn:
        conn.execute("UPDATE dp.pipeline_runs SET heartbeat_at = now() WHERE id = %s AND worker_id = %s",
                     [run_id, worker_id])


def get_run(run_id: int) -> dict | None:
    with db.control() as conn:
        return conn.execute("SELECT * FROM dp.pipeline_runs WHERE id = %s", [run_id]).fetchone()


def is_cancel_requested(run_id: int) -> bool:
    with db.control() as conn:
        row = conn.execute("SELECT cancel_requested_at FROM dp.pipeline_runs WHERE id = %s", [run_id]).fetchone()
    return bool(row and row["cancel_requested_at"])


def request_cancel(run_id: int, requested_by: str) -> dict:
    """Queued runs are cancelled immediately; executing runs are flagged for cooperative cancellation."""
    with db.control() as conn, conn.transaction():
        run = conn.execute("SELECT * FROM dp.pipeline_runs WHERE id = %s FOR UPDATE", [run_id]).fetchone()
        if run is None:
            raise KeyError(run_id)
        status = RunStatus(run["status"])
        if status.is_terminal:
            raise InvalidTransition(f"Run {run_id} already finished ({status})")
        if status in (RunStatus.PENDING, RunStatus.QUEUED):
            conn.execute("""
                UPDATE dp.pipeline_stage_runs SET status = %s, finished_at = now(), updated_at = now()
                WHERE run_id = %s AND status = %s
            """, [StageStatus.CANCELLED, run_id, StageStatus.PENDING])
            return conn.execute("""
                UPDATE dp.pipeline_runs SET status = %s, cancel_requested_at = now(), cancel_requested_by = %s,
                    finished_at = now(), updated_at = now()
                WHERE id = %s RETURNING *
            """, [RunStatus.CANCELLED, requested_by, run_id]).fetchone()
        return conn.execute("""
            UPDATE dp.pipeline_runs SET cancel_requested_at = COALESCE(cancel_requested_at, now()),
                cancel_requested_by = COALESCE(cancel_requested_by, %s), updated_at = now()
            WHERE id = %s RETURNING *
        """, [requested_by, run_id]).fetchone()


def recover_stale_runs(stale_seconds: float) -> list[int]:
    """Runs whose worker stopped heartbeating are failed (and can be retried by the user)."""
    with db.control() as conn, conn.transaction():
        stale = conn.execute("""
            SELECT id, worker_id, heartbeat_at FROM dp.pipeline_runs
            WHERE status = ANY(%s) AND heartbeat_at < now() - make_interval(secs => %s)
            FOR UPDATE SKIP LOCKED
        """, [[str(s) for s in EXECUTING_RUN_STATUSES], stale_seconds]).fetchall()
        for run in stale:
            message = f"Worker {run['worker_id']} stopped responding (last heartbeat {run['heartbeat_at']})"
            stages = conn.execute("""
                UPDATE dp.pipeline_stage_runs SET status = %s, finished_at = now(), error_message = %s, updated_at = now()
                WHERE run_id = %s AND status = ANY(%s) RETURNING id, stage, attempt
            """, [StageStatus.FAILED, message, run["id"], [StageStatus.RUNNING, StageStatus.RETRYING]]).fetchall()
            conn.execute("""
                UPDATE dp.pipeline_stage_runs SET status = %s, finished_at = now(), updated_at = now()
                WHERE run_id = %s AND status = %s
            """, [StageStatus.CANCELLED, run["id"], StageStatus.PENDING])
            conn.execute("""
                INSERT INTO dp.pipeline_errors (run_id, stage_run_id, stage, attempt, exception_type, message, retryable)
                VALUES (%s, %s, %s, %s, 'WorkerLost', %s, true)
            """, [run["id"], stages[0]["id"] if stages else None, stages[0]["stage"] if stages else None,
                  stages[0]["attempt"] if stages else None, message])
            conn.execute("""
                UPDATE dp.pipeline_runs SET status = %s, finished_at = now(), error_summary = %s,
                    error_count = error_count + 1, updated_at = now()
                WHERE id = %s
            """, [RunStatus.FAILED, message, run["id"]])
    return [run["id"] for run in stale]


def list_runs(*, source_id=None, definition_id=None, status=None, country=None, trigger_type=None,
              since=None, until=None, limit=50, offset=0) -> tuple[int, list[dict]]:
    where = []
    params: dict = {}
    for column, value in [("source_id", source_id), ("pipeline_definition_id", definition_id),
                          ("country", country), ("trigger_type", trigger_type)]:
        if value:
            where.append(f"{column} = ANY(%({column})s)")
            params[column] = value if isinstance(value, list) else [value]
    if status:
        where.append("status = ANY(%(status)s)")
        params["status"] = status if isinstance(status, list) else [status]
    if since:
        where.append("created_at >= %(since)s")
        params["since"] = since
    if until:
        where.append("created_at < %(until)s")
        params["until"] = until
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    with db.control() as conn:
        total = conn.execute(f"SELECT count(*) AS n FROM dp.pipeline_runs {clause}", params).fetchone()["n"]
        rows = conn.execute(f"""
            SELECT * FROM dp.pipeline_runs {clause} ORDER BY created_at DESC LIMIT %(limit)s OFFSET %(offset)s
        """, {**params, "limit": limit, "offset": offset}).fetchall()
    return total, rows


# ---------------------------------------------------------------- stages

def get_stages(run_id: int) -> list[dict]:
    with db.control() as conn:
        return conn.execute("SELECT * FROM dp.pipeline_stage_runs WHERE run_id = %s ORDER BY position",
                            [run_id]).fetchall()


def set_stage_status(stage_run_id: int, target: StageStatus, **fields) -> dict:
    if target == StageStatus.RUNNING:
        fields.setdefault("started_at_now", True)
    started_now = fields.pop("started_at_now", False)
    assignments = [f"{name} = %({name})s" for name in fields]
    if started_now:
        assignments.append("started_at = COALESCE(started_at, now())")
    if target.is_terminal:
        assignments.append("finished_at = now()")
    values = {name: (Jsonb(v) if isinstance(v, (dict, list)) else v) for name, v in fields.items()}
    with db.control() as conn:
        row = conn.execute(f"""
            UPDATE dp.pipeline_stage_runs SET status = %(target)s, updated_at = now()
                {", " + ", ".join(assignments) if assignments else ""}
            WHERE id = %(id)s AND status = ANY(%(allowed)s) RETURNING *
        """, {**values, "target": target, "id": stage_run_id, "allowed": allowed_stage_sources(target)}).fetchone()
    if row is None:
        raise InvalidTransition(f"Stage run {stage_run_id} cannot move to {target}")
    return row


def update_stage(stage_run_id: int, **fields) -> None:
    if not fields:
        return
    assignments = ", ".join(f"{name} = %({name})s" for name in fields)
    values = {name: (Jsonb(v) if isinstance(v, (dict, list)) else v) for name, v in fields.items()}
    with db.control() as conn:
        conn.execute(f"UPDATE dp.pipeline_stage_runs SET {assignments}, updated_at = now() WHERE id = %(id)s",
                     {**values, "id": stage_run_id})


def merge_stage_metrics(stage_run_id: int, metrics: dict) -> None:
    with db.control() as conn:
        conn.execute("UPDATE dp.pipeline_stage_runs SET metrics = metrics || %s, updated_at = now() WHERE id = %s",
                     [Jsonb(metrics), stage_run_id])


def cancel_pending_stages(run_id: int) -> None:
    with db.control() as conn:
        conn.execute("""
            UPDATE dp.pipeline_stage_runs SET status = %s, finished_at = now(), updated_at = now()
            WHERE run_id = %s AND status = %s
        """, [StageStatus.CANCELLED, run_id, StageStatus.PENDING])


# ---------------------------------------------------------------- errors, logs, metrics, checkpoints

def record_error(run_id: int, stage_run_id: int | None, stage: str | None, attempt: int | None,
                 error: BaseException, context: dict | None = None) -> None:
    context = {**getattr(error, "context", {}), **(context or {})}
    trace = "".join(tb.format_exception(type(error), error, error.__traceback__))
    with db.control() as conn:
        conn.execute("""
            INSERT INTO dp.pipeline_errors (run_id, stage_run_id, stage, attempt, exception_type, message,
                retryable, context, traceback)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, [run_id, stage_run_id, stage, attempt, type(error).__name__, redact(str(error))[:4000],
              is_retryable(error), Jsonb(redact(context)), redact(trace)[-20000:]])
        conn.execute("UPDATE dp.pipeline_runs SET error_count = error_count + 1 WHERE id = %s", [run_id])


def get_errors(run_id: int) -> list[dict]:
    with db.control() as conn:
        return conn.execute("SELECT * FROM dp.pipeline_errors WHERE run_id = %s ORDER BY id", [run_id]).fetchall()


def write_logs(batch: list[tuple]) -> None:
    with db.control() as conn, conn.cursor() as cur:
        cur.executemany("""
            INSERT INTO dp.pipeline_logs (run_id, stage, level, message, context) VALUES (%s, %s, %s, %s, %s)
        """, [(run_id, stage, level, message, Jsonb(context)) for run_id, stage, level, message, context in batch])


def get_logs(run_id: int, stage: str | None = None, after_id: int = 0, limit: int = 500) -> list[dict]:
    with db.control() as conn:
        return conn.execute("""
            SELECT * FROM dp.pipeline_logs
            WHERE run_id = %(run)s AND id > %(after)s AND (%(stage)s::text IS NULL OR stage = %(stage)s)
            ORDER BY id LIMIT %(limit)s
        """, {"run": run_id, "after": after_id, "stage": stage, "limit": limit}).fetchall()


def record_metrics(run_id: int, stage: str | None, metrics: dict[str, float], labels: dict | None = None) -> None:
    with db.control() as conn, conn.cursor() as cur:
        cur.executemany("""
            INSERT INTO dp.pipeline_metrics (run_id, stage, name, value, labels) VALUES (%s, %s, %s, %s, %s)
        """, [(run_id, stage, name, float(value), Jsonb(labels or {})) for name, value in metrics.items()
              if value is not None])


def get_checkpoint(scope: str, key: str):
    with db.control() as conn:
        row = conn.execute("SELECT value FROM dp.pipeline_checkpoints WHERE scope = %s AND key = %s",
                           [scope, key]).fetchone()
    return row["value"] if row else None


def set_checkpoint(scope: str, key: str, value, run_id: int | None) -> None:
    with db.control() as conn:
        conn.execute("""
            INSERT INTO dp.pipeline_checkpoints (scope, key, value, run_id) VALUES (%s, %s, %s, %s)
            ON CONFLICT (scope, key) DO UPDATE SET value = EXCLUDED.value, run_id = EXCLUDED.run_id, updated_at = now()
        """, [scope, key, Jsonb(value), run_id])


# ---------------------------------------------------------------- artifacts

def create_artifact(**fields) -> dict:
    fields.setdefault("status", "downloading")
    fields["metadata"] = Jsonb(fields.get("metadata") or {})
    columns = ", ".join(fields)
    placeholders = ", ".join(f"%({k})s" for k in fields)
    with db.control() as conn:
        return conn.execute(f"INSERT INTO dp.dataset_artifacts ({columns}) VALUES ({placeholders}) RETURNING *",
                            fields).fetchone()


def update_artifact(artifact_id: int, **fields) -> dict:
    if "metadata" in fields:
        fields["metadata"] = Jsonb(fields["metadata"])
    assignments = ", ".join(f"{k} = %({k})s" for k in fields)
    with db.control() as conn:
        return conn.execute(f"""
            UPDATE dp.dataset_artifacts SET {assignments}, updated_at = now() WHERE id = %(id)s RETURNING *
        """, {**fields, "id": artifact_id}).fetchone()


def get_artifact(artifact_id: int) -> dict | None:
    with db.control() as conn:
        return conn.execute("SELECT * FROM dp.dataset_artifacts WHERE id = %s", [artifact_id]).fetchone()


def find_available_artifact(source_id: str, *, sha256: str | None = None, source_checksum: str | None = None,
                            dataset_name: str | None = None) -> dict | None:
    with db.control() as conn:
        return conn.execute("""
            SELECT * FROM dp.dataset_artifacts
            WHERE source_id = %(source)s AND status = 'available'
              AND (%(sha)s::text IS NULL OR sha256 = %(sha)s)
              AND (%(checksum)s::text IS NULL OR source_checksum = %(checksum)s)
              AND (%(dataset)s::text IS NULL OR dataset_name = %(dataset)s)
            ORDER BY created_at DESC LIMIT 1
        """, {"source": source_id, "sha": sha256, "checksum": source_checksum, "dataset": dataset_name}).fetchone()


def list_artifacts(source_id: str | None = None, limit: int = 100) -> list[dict]:
    with db.control() as conn:
        return conn.execute("""
            SELECT * FROM dp.dataset_artifacts WHERE %(s)s::text IS NULL OR source_id = %(s)s
            ORDER BY created_at DESC LIMIT %(limit)s
        """, {"s": source_id, "limit": limit}).fetchall()


# ---------------------------------------------------------------- dataset catalog

def register_dataset(dataset_id: str, layer: str, name: str, description: str, source_id: str | None,
                     partition_columns: list[str], serving_table: str | None = None) -> None:
    with db.control() as conn:
        conn.execute("""
            INSERT INTO dp.datasets (id, layer, name, description, source_id, partition_columns, serving_table)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET layer = EXCLUDED.layer, name = EXCLUDED.name,
                description = EXCLUDED.description, source_id = EXCLUDED.source_id,
                partition_columns = EXCLUDED.partition_columns, serving_table = EXCLUDED.serving_table,
                updated_at = now()
        """, [dataset_id, layer, name, description, source_id, partition_columns, serving_table])


def begin_version(dataset_id: str, partition: dict, location: str, run_id: int | None, versions: dict,
                  inputs: list[dict]) -> dict:
    with db.control() as conn:
        return conn.execute("""
            INSERT INTO dp.dataset_versions (dataset_id, partition, partition_key, status, location, versions,
                inputs, pipeline_run_id)
            VALUES (%s, %s, %s, 'building', %s, %s, %s, %s) RETURNING *
        """, [dataset_id, Jsonb(partition), partition_key(partition), location, Jsonb(versions), Jsonb(inputs),
              run_id]).fetchone()


def commit_version(version_id: int, *, file_count: int, row_count: int, size_bytes: int, schema: dict | None,
                   schema_hash: str | None, location: str | None = None) -> dict:
    """Make a version the current one for its partition (atomically superseding the previous)."""
    with db.control() as conn, conn.transaction():
        version = conn.execute("SELECT * FROM dp.dataset_versions WHERE id = %s FOR UPDATE", [version_id]).fetchone()
        conn.execute("""
            UPDATE dp.dataset_versions SET is_current = false
            WHERE dataset_id = %s AND partition_key = %s AND is_current
        """, [version["dataset_id"], version["partition_key"]])
        return conn.execute("""
            UPDATE dp.dataset_versions SET status = 'committed', is_current = true, committed_at = now(),
                file_count = %s, row_count = %s, size_bytes = %s, schema = %s, schema_hash = %s,
                location = COALESCE(%s, location)
            WHERE id = %s RETURNING *
        """, [file_count, row_count, size_bytes, Jsonb(schema) if schema is not None else None, schema_hash,
              location, version_id]).fetchone()


def fail_version(version_id: int) -> None:
    with db.control() as conn:
        conn.execute("UPDATE dp.dataset_versions SET status = 'failed' WHERE id = %s AND status = 'building'",
                     [version_id])


def current_versions(dataset_id: str, partition: dict | None = None) -> list[dict]:
    """Current committed versions of a dataset; `partition` filters on a subset of partition columns."""
    with db.control() as conn:
        return conn.execute("""
            SELECT * FROM dp.dataset_versions
            WHERE dataset_id = %s AND is_current AND partition @> %s
            ORDER BY partition_key
        """, [dataset_id, Jsonb(partition or {})]).fetchall()


def get_version(version_id: int) -> dict | None:
    with db.control() as conn:
        return conn.execute("SELECT * FROM dp.dataset_versions WHERE id = %s", [version_id]).fetchone()


def list_versions(dataset_id: str, limit: int = 50) -> list[dict]:
    with db.control() as conn:
        return conn.execute("""
            SELECT * FROM dp.dataset_versions WHERE dataset_id = %s ORDER BY created_at DESC LIMIT %s
        """, [dataset_id, limit]).fetchall()


def list_datasets() -> list[dict]:
    with db.control() as conn:
        return conn.execute("""
            SELECT d.*,
                   COALESCE(sum(v.row_count) FILTER (WHERE v.is_current), 0)::bigint AS row_count,
                   COALESCE(sum(v.size_bytes) FILTER (WHERE v.is_current), 0)::bigint AS size_bytes,
                   count(v.*) FILTER (WHERE v.is_current) AS partitions,
                   max(v.committed_at) AS last_committed_at
            FROM dp.datasets d
            LEFT JOIN dp.dataset_versions v ON v.dataset_id = d.id
            GROUP BY d.id ORDER BY array_position(ARRAY['bronze','silver','gold'], d.layer), d.id
        """).fetchall()


# ---------------------------------------------------------------- quality & serving state

def record_quality_results(results: list[dict]) -> None:
    if not results:
        return
    with db.control() as conn, conn.cursor() as cur:
        cur.executemany("""
            INSERT INTO dp.quality_results (pipeline_run_id, stage_run_id, stage, dataset_id, dataset_version_id,
                check_name, check_type, severity, status, records_checked, records_failed, failure_percentage,
                threshold_percentage, details)
            VALUES (%(pipeline_run_id)s, %(stage_run_id)s, %(stage)s, %(dataset_id)s, %(dataset_version_id)s,
                %(check_name)s, %(check_type)s, %(severity)s, %(status)s, %(records_checked)s, %(records_failed)s,
                %(failure_percentage)s, %(threshold_percentage)s, %(details)s)
        """, [{**r, "details": Jsonb(r.get("details") or {})} for r in results])


def get_quality_results(run_id: int) -> list[dict]:
    with db.control() as conn:
        return conn.execute("SELECT * FROM dp.quality_results WHERE pipeline_run_id = %s ORDER BY id",
                            [run_id]).fetchall()


def get_serving_state(dataset_id: str, partition_key_value: str) -> dict | None:
    with db.control() as conn:
        return conn.execute("SELECT * FROM dp.serving_state WHERE dataset_id = %s AND partition_key = %s",
                            [dataset_id, partition_key_value]).fetchone()


def set_serving_state(dataset_id: str, partition_key_value: str, version_id: int, row_count: int,
                      run_id: int | None) -> None:
    with db.control() as conn:
        conn.execute("""
            INSERT INTO dp.serving_state (dataset_id, partition_key, dataset_version_id, row_count, pipeline_run_id)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (dataset_id, partition_key) DO UPDATE SET dataset_version_id = EXCLUDED.dataset_version_id,
                row_count = EXCLUDED.row_count, pipeline_run_id = EXCLUDED.pipeline_run_id, published_at = now()
        """, [dataset_id, partition_key_value, version_id, row_count, run_id])
