"""Executes one pipeline run: stages in order, each with its own status, retries and errors."""

from __future__ import annotations

import random
import threading
import time
from contextlib import nullcontext

from .. import repository
from ..config import get_settings
from ..errors import PermanentError, RunCancelled, is_retryable
from ..gold.version import GOLD_VERSION
from ..lake.reader import forget_run, interrupt_run_queries
from ..observability import WORKER_ID, get_logger, log_context
from ..sources.base import DataSourceAdapter, StagePolicy, StageResult
from ..sources.registry import find_pipeline
from ..states import STAGE_RUN_STATUS, RunStatus, Stage, StageStatus
from ..storage import get_storage
from .context import StageContext
from .locks import GOLD_SCOPE_STAGES, gold_scope_lock
from .stages import STAGE_HANDLERS

log = get_logger(__name__)


def backoff_delay(policy: StagePolicy, attempt: int) -> float:
    """Exponential backoff with full jitter, bounded by max_delay."""
    ceiling = min(policy.max_delay_seconds, policy.base_delay_seconds * (2 ** (attempt - 1)))
    return random.uniform(ceiling / 2, ceiling)


class Heartbeat:
    def __init__(self, run_id: int, interval: float):
        self.run_id = run_id
        self.interval = interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name=f"heartbeat-{run_id}", daemon=True)

    def _loop(self):
        while not self._stop.wait(self.interval):
            try:
                repository.heartbeat(self.run_id, WORKER_ID)
                # Long engine calls cannot check for cancellation themselves; interrupt them from here.
                if repository.is_cancel_requested(self.run_id) and interrupt_run_queries(self.run_id):
                    log.info("interrupted running queries for cancellation")
            except Exception:
                log.warning("heartbeat failed", exc_info=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(timeout=5)
        forget_run(self.run_id)


def execute_run(run: dict) -> RunStatus:
    """Run a claimed run to a terminal status. Never raises for pipeline failures."""
    settings = get_settings()
    storage = get_storage()
    adapter, pipeline = find_pipeline(run["pipeline_definition_id"])
    state = dict(run.get("outputs") or {})
    optional_failures: list[str] = []

    with log_context(pipeline_run_id=run["id"], source=run["source_id"], country=run.get("country"),
                     dataset=run.get("dataset"), worker=WORKER_ID), Heartbeat(run["id"], settings.heartbeat_seconds):
        log.info("run started", pipeline=run["pipeline_definition_id"])
        # Record the code versions this worker actually executes with (the API may run newer or older code).
        executing = {"parser": adapter.parser_version, "transformation": adapter.transformation_version,
                     "entity_resolution": run["parameters"].get("er_algorithm", "v1"), "gold": GOLD_VERSION}
        if executing != {k: run["versions"].get(k) for k in executing}:
            log.warning("worker code versions differ from those recorded at queue time; recording the executed ones",
                        queued=run["versions"], executing=executing)
        repository.update_run(run["id"], versions={**run["versions"], **executing, "worker": WORKER_ID})
        try:
            params = adapter.validate_parameters(pipeline, run["parameters"])
        except PermanentError as error:
            repository.record_error(run["id"], None, None, None, error)
            repository.cancel_pending_stages(run["id"])
            repository.set_run_status(run["id"], RunStatus.FAILED, error_summary=str(error))
            return RunStatus.FAILED

        for stage_row in repository.get_stages(run["id"]):
            if StageStatus(stage_row["status"]).is_terminal:
                continue
            stage = Stage(stage_row["stage"])
            repository.set_run_status(run["id"], STAGE_RUN_STATUS[stage], current_stage=str(stage))
            policy = adapter.stage_policy(stage)

            with log_context(stage=str(stage), stage_run_id=stage_row["id"]):
                outcome = _run_stage(adapter, run, params, stage, stage_row, policy, storage, settings, state)

            if outcome == StageStatus.CANCELLED:
                repository.cancel_pending_stages(run["id"])
                repository.set_run_status(run["id"], RunStatus.CANCELLED, current_stage=None)
                log.info("run cancelled")
                return RunStatus.CANCELLED
            if outcome == StageStatus.FAILED:
                if policy.required:
                    repository.cancel_pending_stages(run["id"])
                    summary = repository.get_stages(run["id"])[stage_row["position"]]["error_message"]
                    repository.set_run_status(run["id"], RunStatus.FAILED, current_stage=None,
                                              error_summary=f"{stage}: {summary}")
                    log.error("run failed", failed_stage=str(stage))
                    return RunStatus.FAILED
                optional_failures.append(str(stage))

        final = RunStatus.PARTIALLY_COMPLETED if optional_failures else RunStatus.COMPLETED
        repository.set_run_status(run["id"], final, current_stage=None,
                                  error_summary=(f"Optional stages failed: {', '.join(optional_failures)}"
                                                 if optional_failures else None))
        log.info("run finished", status=str(final))
        return final


def _run_stage(adapter: DataSourceAdapter, run: dict, params: dict, stage: Stage, stage_row: dict,
               policy: StagePolicy, storage, settings, state: dict) -> StageStatus:
    handler = STAGE_HANDLERS[stage]
    attempt = stage_row["attempt"]

    while True:
        attempt += 1
        if attempt == 1 and StageStatus(stage_row["status"]) == StageStatus.PENDING:
            repository.set_stage_status(stage_row["id"], StageStatus.RUNNING, attempt=attempt,
                                        max_attempts=policy.max_attempts)
        else:
            repository.set_stage_status(stage_row["id"], StageStatus.RUNNING, attempt=attempt, next_retry_at=None)

        ctx = StageContext(run=run, params=params, stage=stage, stage_run_id=stage_row["id"], attempt=attempt,
                           storage=storage, settings=settings, state=state)
        started = time.monotonic()
        try:
            ctx.check_cancelled(force=True)
            with gold_scope_lock(ctx) if stage in GOLD_SCOPE_STAGES else nullcontext():
                result: StageResult = handler(adapter, ctx)
        except RunCancelled:
            ctx.run_cleanups()
            repository.set_stage_status(stage_row["id"], StageStatus.CANCELLED)
            return StageStatus.CANCELLED
        except Exception as error:  # every failure is recorded; retry decision by error class
            if type(error).__name__ == "InterruptException" and repository.is_cancel_requested(run["id"]):
                ctx.run_cleanups()                  # a query interrupted by the cancellation watchdog
                repository.set_stage_status(stage_row["id"], StageStatus.CANCELLED)
                return StageStatus.CANCELLED
            ctx.run_cleanups()
            retryable = is_retryable(error)
            repository.record_error(run["id"], stage_row["id"], str(stage), attempt, error)
            log.error("stage attempt failed", attempt=attempt, retryable=retryable,
                      error_type=type(error).__name__, error=str(error))
            if retryable and attempt < policy.max_attempts:
                delay = backoff_delay(policy, attempt)
                repository.set_stage_status(stage_row["id"], StageStatus.RETRYING, error_message=str(error)[:2000],
                                            next_retry_at=repository.now() + _seconds(delay))
                if _sleep_unless_cancelled(run["id"], delay):
                    repository.set_stage_status(stage_row["id"], StageStatus.CANCELLED)
                    return StageStatus.CANCELLED
                continue
            repository.set_stage_status(stage_row["id"], StageStatus.FAILED, error_message=str(error)[:2000])
            return StageStatus.FAILED

        ctx.clear_cleanups()
        elapsed = time.monotonic() - started
        metrics = {**result.metrics, "duration_seconds": round(elapsed, 3)}
        if result.records_out and elapsed > 0:
            metrics.setdefault("records_per_sec", round(result.records_out / elapsed, 1))
        status = StageStatus.SKIPPED if result.skipped_reason else StageStatus.SUCCEEDED
        repository.set_stage_status(
            stage_row["id"], status, skip_reason=result.skipped_reason, records_in=result.records_in,
            records_out=result.records_out, records_rejected=result.records_rejected,
            bytes_processed=result.bytes_processed, metrics=metrics,
        )
        repository.record_metrics(run["id"], str(stage), {
            "stage_duration_seconds": elapsed,
            "records_processed_total": result.records_out or 0,
            "records_rejected_total": result.records_rejected or 0,
            "processing_rate": metrics.get("records_per_sec") or 0,
        }, {"source": run["source_id"], "skipped": bool(result.skipped_reason)})
        log.info("stage finished", status=str(status), skip_reason=result.skipped_reason,
                 records_out=result.records_out, seconds=round(elapsed, 2))
        return status


def _seconds(value: float):
    from datetime import timedelta

    return timedelta(seconds=value)


def _sleep_unless_cancelled(run_id: int, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if repository.is_cancel_requested(run_id):
            return True
        time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
    return repository.is_cancel_requested(run_id)
