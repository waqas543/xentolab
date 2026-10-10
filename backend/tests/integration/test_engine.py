"""Orchestration engine: retries, failures, cancellation, concurrency, recovery, scheduling.

Uses a small test-only adapter whose stages can be told to fail or block.
"""

import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from data_platform import db, repository
from data_platform.engine import scheduler
from data_platform.engine.service import retry_run, start_run
from data_platform.errors import InvalidTransition, ParameterError, PermanentError, TransientError
from data_platform.sources import registry
from data_platform.sources.base import DataSourceAdapter, DiscoveredDataset, PipelineSpec, StagePolicy, StageResult
from data_platform.states import Stage

from .conftest import run_to_completion, stage_map

pytestmark = pytest.mark.integration

BEHAVIOUR: dict = {}


class EngineTestAdapter(DataSourceAdapter):
    source_id = "enginetest"
    name = "Engine test source"

    def pipelines(self):
        return [PipelineSpec(id="enginetest.basic", name="Basic", description="",
                             parameters={"scope": {"type": "string", "default": "A"}},
                             stages=[Stage.DISCOVER, Stage.DOWNLOAD, Stage.BRONZE_INGEST, Stage.ANALYTICS_REFRESH])]

    def concurrency_key(self, params):
        return f"enginetest:{params['scope']}"

    def run_labels(self, params):
        return params["scope"], "test"

    def stage_policy(self, stage):
        if stage == Stage.DOWNLOAD:
            return StagePolicy(max_attempts=3, base_delay_seconds=0.05, max_delay_seconds=0.1)
        if stage == Stage.ANALYTICS_REFRESH:
            return StagePolicy(max_attempts=1, required=False)
        return StagePolicy()

    def discover_available_datasets(self, ctx):
        return [DiscoveredDataset(dataset_name="test", url=None)]

    def download(self, ctx):
        BEHAVIOUR.setdefault("download_calls", 0)
        BEHAVIOUR["download_calls"] += 1
        if BEHAVIOUR["download_calls"] <= BEHAVIOUR.get("transient_failures", 0):
            raise TransientError("connection reset by peer")
        if BEHAVIOUR.get("permanent"):
            raise PermanentError("404 Not Found")
        artifact = repository.create_artifact(source_id=self.source_id, dataset_name="test", filename="x",
                                              format="test", status="available",
                                              sha256=f"{time.time_ns()}")
        ctx.put(artifact_id=artifact["id"])
        return StageResult(records_out=1)

    def validate_download(self, artifact, ctx):
        return StageResult()

    def ingest_bronze(self, artifact, ctx):
        if BEHAVIOUR.get("block"):
            BEHAVIOUR["started"].set()
            for i in range(600):
                ctx.progress(i, None, "records")       # cooperative cancellation point
                time.sleep(0.05)
        return StageResult(records_out=10)

    def transform_silver(self, ctx):
        return StageResult()

    def collect_metrics(self, ctx):
        if BEHAVIOUR.get("metrics_fail"):
            raise RuntimeError("metrics backend unavailable")
        return {"rows": 10}


@pytest.fixture(autouse=True)
def adapter(synced):
    registry.register(EngineTestAdapter())
    registry.sync_to_control_plane()
    BEHAVIOUR.clear()
    from data_platform.engine import stages

    original = stages.STAGE_HANDLERS[Stage.ANALYTICS_REFRESH]
    # The real analytics stage publishes Gold; for this source only adapter metrics matter.
    stages.STAGE_HANDLERS[Stage.ANALYTICS_REFRESH] = lambda a, ctx: StageResult(metrics=a.collect_metrics(ctx))
    yield
    stages.STAGE_HANDLERS[Stage.ANALYTICS_REFRESH] = original
    with db.control() as conn:
        conn.execute("DELETE FROM dp.pipeline_runs WHERE source_id = 'enginetest' AND status <> ALL(%s)",
                     [["COMPLETED", "FAILED", "CANCELLED", "PARTIALLY_COMPLETED"]])


def start(**params):
    return start_run("enginetest.basic", params, trigger_type="manual", triggered_by="test")


def test_transient_failures_are_retried_with_backoff():
    BEHAVIOUR["transient_failures"] = 2
    run = run_to_completion(start()["id"])
    assert run["status"] == "COMPLETED"
    download = stage_map(run["id"])["DOWNLOAD"]
    assert download["status"] == "SUCCEEDED" and download["attempt"] == 3
    errors = repository.get_errors(run["id"])
    assert [(e["stage"], e["attempt"], e["retryable"]) for e in errors] == [("DOWNLOAD", 1, True), ("DOWNLOAD", 2, True)]


def test_retries_are_bounded():
    BEHAVIOUR["transient_failures"] = 10
    run = run_to_completion(start()["id"])
    assert run["status"] == "FAILED"
    assert stage_map(run["id"])["DOWNLOAD"]["attempt"] == 3
    assert stage_map(run["id"])["BRONZE_INGEST"]["status"] == "CANCELLED"


def test_permanent_errors_are_not_retried():
    BEHAVIOUR["permanent"] = True
    run = run_to_completion(start()["id"])
    assert run["status"] == "FAILED" and "404" in run["error_summary"]
    assert stage_map(run["id"])["DOWNLOAD"]["attempt"] == 1
    error = repository.get_errors(run["id"])[0]
    assert error["exception_type"] == "PermanentError" and not error["retryable"] and error["traceback"]


def test_optional_stage_failure_gives_partial_completion():
    BEHAVIOUR["metrics_fail"] = True
    run = run_to_completion(start()["id"])
    assert run["status"] == "PARTIALLY_COMPLETED"
    assert stage_map(run["id"])["BRONZE_INGEST"]["status"] == "SUCCEEDED"


def test_cooperative_cancellation_of_a_running_stage():
    BEHAVIOUR.update(block=True, started=threading.Event())
    run = start()
    worker_thread = threading.Thread(target=run_to_completion, args=(run["id"],))
    worker_thread.start()
    assert BEHAVIOUR["started"].wait(10)
    repository.request_cancel(run["id"], "test")
    worker_thread.join(15)
    final = repository.get_run(run["id"])
    assert final["status"] == "CANCELLED" and final["cancel_requested_by"] == "test"
    stages = stage_map(run["id"])
    assert stages["BRONZE_INGEST"]["status"] == "CANCELLED"
    assert stages["BRONZE_INGEST"]["progress_current"] > 0
    assert stages["ANALYTICS_REFRESH"]["status"] == "CANCELLED"


def test_cancelling_a_queued_run_is_immediate():
    run = start()
    cancelled = repository.request_cancel(run["id"], "test")
    assert cancelled["status"] == "CANCELLED"
    with pytest.raises(InvalidTransition):
        repository.request_cancel(run["id"], "test")


def test_one_executing_run_per_concurrency_key():
    first, second, other = start(scope="K"), start(scope="K"), start(scope="Z")
    claimed = repository.claim_next_run("w1")
    assert claimed["id"] == first["id"]
    # The second run for scope K must wait; scope Z can go.
    assert repository.claim_next_run("w2")["id"] == other["id"]
    assert repository.claim_next_run("w3") is None
    for run in (first, second, other):
        try:
            repository.request_cancel(run["id"], "cleanup")
        except InvalidTransition:
            pass


def test_stale_runs_are_failed():
    run = start(scope="S")
    claimed = repository.claim_next_run("dead-worker")
    assert claimed["id"] == run["id"]
    with db.control() as conn:
        conn.execute("UPDATE dp.pipeline_runs SET heartbeat_at = now() - interval '1 hour' WHERE id = %s", [run["id"]])
    assert run["id"] in repository.recover_stale_runs(60)
    final = repository.get_run(run["id"])
    assert final["status"] == "FAILED" and "stopped responding" in final["error_summary"]
    assert repository.get_errors(run["id"])[-1]["exception_type"] == "WorkerLost"


def test_retry_creates_a_linked_run():
    BEHAVIOUR["permanent"] = True
    failed = run_to_completion(start()["id"])
    BEHAVIOUR.clear()
    retried = retry_run(failed["id"], "test")
    assert retried["retry_of_run_id"] == failed["id"] and retried["trigger_type"] == "retry"
    assert run_to_completion(retried["id"])["status"] == "COMPLETED"
    with pytest.raises(InvalidTransition):
        retry_run(retried["id"], "test")                 # completed runs cannot be retried


def test_parameters_are_validated():
    with pytest.raises(ParameterError):
        start_run("enginetest.basic", {"unknown": 1}, trigger_type="manual", triggered_by="test")
    with pytest.raises(ParameterError):
        start_run("enginetest.basic", {"start_stage": "NOPE"}, trigger_type="manual", triggered_by="test")


def test_scheduler_enqueues_due_schedules_once():
    schedule = scheduler.upsert_schedule("enginetest.basic", "nightly", {"scope": "SCHED"}, "daily")
    assert schedule["next_run_at"] > datetime.now(timezone.utc)
    with db.control() as conn:
        conn.execute("UPDATE dp.pipeline_schedules SET next_run_at = now() - interval '1 minute' WHERE id = %s",
                     [schedule["id"]])
    created = scheduler.tick()
    assert len(created) == 1
    assert repository.get_run(created[0])["trigger_type"] == "schedule"
    assert scheduler.tick() == []                      # next run moved to tomorrow
    row = [s for s in scheduler.list_schedules() if s["id"] == schedule["id"]][0]
    assert row["next_run_at"] > datetime.now(timezone.utc) + timedelta(hours=1)
    repository.request_cancel(created[0], "cleanup")
    with db.control() as conn:
        conn.execute("UPDATE dp.pipeline_schedules SET enabled = false WHERE id = %s", [schedule["id"]])


def test_cancellation_interrupts_a_blocking_engine_query(monkeypatch):
    """A stage stuck inside one long DuckDB query (no progress callbacks) is still cancellable."""
    from data_platform.lake.reader import duck

    def slow_bronze(self, artifact, ctx):
        BEHAVIOUR["started"].set()
        con = duck()
        con.execute("SELECT count(*) FROM range(10000000000) a")      # minutes of work, no checkpoints
        return StageResult(records_out=1)

    monkeypatch.setattr(EngineTestAdapter, "ingest_bronze", slow_bronze)
    monkeypatch.setenv("DATA_PLATFORM_HEARTBEAT_SECONDS", "0.5")
    from data_platform.config import get_settings

    get_settings.cache_clear()
    BEHAVIOUR.update(started=threading.Event())
    run = start(scope="INT")
    thread = threading.Thread(target=run_to_completion, args=(run["id"],))
    thread.start()
    assert BEHAVIOUR["started"].wait(10)
    time.sleep(0.5)
    repository.request_cancel(run["id"], "test")
    thread.join(20)
    get_settings.cache_clear()
    assert not thread.is_alive()
    assert repository.get_run(run["id"])["status"] == "CANCELLED"
    assert stage_map(run["id"])["BRONZE_INGEST"]["status"] == "CANCELLED"
