"""Control-plane APIs: sources, pipelines, runs (create / cancel / retry / live events), artifacts, schedules."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel

from .. import db, repository
from ..config import get_settings
from ..engine import scheduler
from ..engine.service import retry_run, start_run
from ..errors import InvalidTransition, ParameterError
from ..query.service import jsonable
from ..sources.registry import adapters, counted_artifacts_sql
from ..states import RunStatus
from .auth import (
    CANCEL_PIPELINE,
    CONFIGURE_SOURCE,
    RUN_PIPELINE,
    VIEW,
    Principal,
    require,
)

router = APIRouter(prefix="/api/data-platform", tags=["data-platform"])
Viewer = Annotated[Principal, Depends(require(VIEW))]


def _duration(run: dict) -> float | None:
    if not run.get("started_at"):
        return None
    end = run.get("finished_at") or datetime.now(timezone.utc)
    return round((end - run["started_at"]).total_seconds(), 1)


def serialize_run(run: dict) -> dict:
    data = jsonable(run)
    data["duration_seconds"] = _duration(run)
    data["is_terminal"] = RunStatus(run["status"]).is_terminal
    return data


def serialize_stage(stage: dict) -> dict:
    data = jsonable(stage)
    duration = None
    if stage["started_at"]:
        end = stage["finished_at"] or datetime.now(timezone.utc)
        duration = round((end - stage["started_at"]).total_seconds(), 1)
    data["duration_seconds"] = duration
    total = stage["progress_total"]
    # A percentage only when the total is genuinely known.
    data["progress_percent"] = (round(100 * stage["progress_current"] / total, 1)
                                if total and stage["progress_current"] is not None else None)
    return data


# ---------------------------------------------------------------- sources & pipelines

@router.get("/sources")
def list_sources(_: Viewer):
    with db.control() as conn:
        sources = conn.execute("SELECT * FROM dp.data_sources ORDER BY id").fetchall()
        runs = conn.execute("""
            SELECT DISTINCT ON (source_id) source_id, id, status, created_at, finished_at
            FROM dp.pipeline_runs ORDER BY source_id, created_at DESC
        """).fetchall()
        successes = conn.execute("""
            SELECT DISTINCT ON (source_id) source_id, id, finished_at FROM dp.pipeline_runs
            WHERE status IN ('COMPLETED', 'PARTIALLY_COMPLETED') ORDER BY source_id, finished_at DESC
        """).fetchall()
        failures = conn.execute("""
            SELECT DISTINCT ON (source_id) source_id, id, finished_at, error_summary FROM dp.pipeline_runs
            WHERE status = 'FAILED' ORDER BY source_id, finished_at DESC
        """).fetchall()
        artifacts = conn.execute("""
            SELECT DISTINCT ON (source_id) source_id, id, filename, source_version, size_bytes, created_at
            FROM dp.dataset_artifacts WHERE status = 'available' ORDER BY source_id, created_at DESC
        """).fetchall()
        storage = conn.execute("""
            SELECT source_id, sum(size_bytes)::bigint AS bytes FROM (
                SELECT source_id, size_bytes FROM dp.dataset_artifacts WHERE status = 'available'
                UNION ALL
                SELECT coalesce(d.source_id, v.partition->>'source'), v.size_bytes
                FROM dp.dataset_versions v JOIN dp.datasets d ON d.id = v.dataset_id
                WHERE v.status = 'committed'
            ) s WHERE source_id IS NOT NULL GROUP BY source_id
        """).fetchall()
        # Bronze = current artifacts (latest snapshot per source and country; every window for accumulating sources).
        records = conn.execute(f"""
            WITH latest AS ({counted_artifacts_sql()})
            SELECT l.source_id, 'bronze' AS layer, sum(v.row_count)::bigint AS rows
            FROM dp.dataset_versions v JOIN latest l ON (v.partition->>'artifact_id')::bigint = l.id
            WHERE v.is_current GROUP BY 1
            UNION ALL
            SELECT v.partition->>'source', 'silver', sum(v.row_count)::bigint
            FROM dp.dataset_versions v
            WHERE v.dataset_id LIKE 'silver.%' AND v.dataset_id <> 'silver.rejects' AND v.is_current GROUP BY 1
        """).fetchall()
        schedules = conn.execute("""
            SELECT s.*, p.source_id FROM dp.pipeline_schedules s
            JOIN dp.pipeline_definitions p ON p.id = s.pipeline_definition_id WHERE s.enabled
        """).fetchall()

    by = lambda rows: {r["source_id"]: r for r in rows}  # noqa: E731
    last, success, failure, artifact, used = by(runs), by(successes), by(failures), by(artifacts), by(storage)
    result = []
    for source in sources:
        sid = source["id"]
        adapter = adapters().get(sid)
        result.append(jsonable({
            **source,
            "registered": adapter is not None,
            "status": last.get(sid, {}).get("status"),
            "last_run": last.get(sid),
            "last_success": success.get(sid),
            "last_failure": failure.get(sid),
            "latest_artifact": artifact.get(sid),
            "storage_bytes": used.get(sid, {}).get("bytes", 0),
            "records": {r["layer"]: r["rows"] for r in records if r["source_id"] == sid},
            "schedules": [s for s in schedules if s["source_id"] == sid],
            "pipelines": [p for p in repository.list_definitions(sid)],
            # Which credentials the adapter needs and whether the worker environment has them — never values.
            "credentials": [{"name": f"DP_SECRET_{name}", "configured": bool(get_settings().secret(name))}
                            for name in (adapter.capabilities.credentials if adapter else ())],
        }))
    return result


class SourceSettings(BaseModel):
    enabled: bool


@router.patch("/sources/{source_id}")
def update_source(source_id: str, body: SourceSettings,
                  _: Annotated[Principal, Depends(require(CONFIGURE_SOURCE))]):
    row = repository.set_source_enabled(source_id, body.enabled)
    if row is None:
        raise HTTPException(404, "Unknown source")
    return jsonable(row)


@router.get("/pipelines")
def list_pipelines(_: Viewer):
    return jsonable(repository.list_definitions())


class PipelineSettings(BaseModel):
    enabled: bool | None = None
    quality_overrides: dict[str, dict[str, dict[str, Any]]] | None = None


@router.patch("/pipelines/{definition_id}")
def update_pipeline(definition_id: str, body: PipelineSettings,
                    _: Annotated[Principal, Depends(require(CONFIGURE_SOURCE))]):
    row = repository.update_definition_settings(definition_id, body.enabled, body.quality_overrides)
    if row is None:
        raise HTTPException(404, "Unknown pipeline")
    return jsonable(row)


class RunRequest(BaseModel):
    parameters: dict[str, Any] = {}


@router.post("/pipelines/{definition_id}/runs", status_code=202)
def create_run(definition_id: str, body: RunRequest,
               principal: Annotated[Principal, Depends(require(RUN_PIPELINE))]):
    try:
        run = start_run(definition_id, body.parameters, trigger_type="manual", triggered_by=principal.name)
    except KeyError as error:
        raise HTTPException(404, str(error)) from None
    except ParameterError as error:
        raise HTTPException(422, str(error)) from None
    return serialize_run(run)


# ---------------------------------------------------------------- runs

@router.get("/runs")
def list_runs(_: Viewer,
              source: Annotated[list[str] | None, Query()] = None,
              pipeline: Annotated[list[str] | None, Query()] = None,
              status: Annotated[list[str] | None, Query()] = None,
              country: Annotated[list[str] | None, Query()] = None,
              trigger: Annotated[list[str] | None, Query()] = None,
              since: datetime | None = None, until: datetime | None = None,
              page: Annotated[int, Query(ge=1)] = 1, page_size: Annotated[int, Query(ge=1, le=200)] = 50):
    total, rows = repository.list_runs(source_id=source, definition_id=pipeline, status=status, country=country,
                                       trigger_type=trigger, since=since, until=until, limit=page_size,
                                       offset=(page - 1) * page_size)
    return {"total": total, "page": page, "page_size": page_size, "rows": [serialize_run(r) for r in rows]}


def run_detail(run_id: int) -> dict:
    run = repository.get_run(run_id)
    if run is None:
        raise HTTPException(404, "Run not found")
    return {"run": serialize_run(run), "stages": [serialize_stage(s) for s in repository.get_stages(run_id)]}


@router.get("/runs/{run_id}")
def get_run(run_id: int, _: Viewer):
    detail = run_detail(run_id)
    detail["errors"] = jsonable(repository.get_errors(run_id))
    detail["quality"] = jsonable(repository.get_quality_results(run_id))
    artifact_id = detail["run"]["outputs"].get("artifact_id") or detail["run"]["parameters"].get("artifact_id")
    detail["artifact"] = jsonable(repository.get_artifact(artifact_id)) if artifact_id else None
    return detail


@router.get("/runs/{run_id}/stages")
def get_stages(run_id: int, _: Viewer):
    return [serialize_stage(s) for s in repository.get_stages(run_id)]


@router.get("/runs/{run_id}/logs")
def get_logs(run_id: int, _: Viewer, stage: str | None = None, after_id: int = 0,
             limit: Annotated[int, Query(ge=1, le=2000)] = 500):
    return jsonable(repository.get_logs(run_id, stage, after_id, limit))


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: int, principal: Annotated[Principal, Depends(require(CANCEL_PIPELINE))]):
    try:
        return serialize_run(repository.request_cancel(run_id, principal.name))
    except KeyError:
        raise HTTPException(404, "Run not found") from None
    except InvalidTransition as error:
        raise HTTPException(409, str(error)) from None


@router.post("/runs/{run_id}/retry", status_code=202)
def retry(run_id: int, principal: Annotated[Principal, Depends(require(RUN_PIPELINE))]):
    try:
        return serialize_run(retry_run(run_id, principal.name))
    except KeyError:
        raise HTTPException(404, "Run not found") from None
    except (InvalidTransition, ParameterError) as error:
        raise HTTPException(409, str(error)) from None


@router.get("/runs/{run_id}/events")
def run_events(run_id: int, _: Viewer):
    """Server-sent events: a snapshot whenever the run or a stage changes; ends when the run finishes."""

    def stream():
        last_signature = None
        idle = 0
        while True:
            with db.control() as conn:
                signature = conn.execute("""
                    SELECT r.status, r.updated_at, max(s.updated_at) AS stages_updated
                    FROM dp.pipeline_runs r LEFT JOIN dp.pipeline_stage_runs s ON s.run_id = r.id
                    WHERE r.id = %s GROUP BY r.id
                """, [run_id]).fetchone()
            if signature is None:
                yield "event: error\ndata: {\"message\": \"run not found\"}\n\n"
                return
            if signature != last_signature:
                last_signature = signature
                idle = 0
                yield f"data: {json.dumps(run_detail(run_id), default=str)}\n\n"
                if RunStatus(signature["status"]).is_terminal:
                    yield "event: end\ndata: {}\n\n"
                    return
            else:
                idle += 1
                if idle % 15 == 0:
                    yield ": keep-alive\n\n"
            time.sleep(1)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/runs/{run_id}/lineage")
def run_lineage(run_id: int, _: Viewer):
    """Artifact -> Bronze -> Silver -> entity resolution -> Gold -> serving, for this run's outputs."""
    run = repository.get_run(run_id)
    if run is None:
        raise HTTPException(404, "Run not found")
    outputs = run["outputs"]
    nodes, edges = [], []

    def add(node_id, kind, label, detail=None):
        if not any(n["id"] == node_id for n in nodes):
            nodes.append({"id": node_id, "kind": kind, "label": label, "detail": jsonable(detail or {})})

    artifact_id = outputs.get("artifact_id") or run["parameters"].get("artifact_id")
    if artifact_id:
        artifact = repository.get_artifact(artifact_id)
        add(f"artifact:{artifact_id}", "artifact", artifact["filename"],
            {"size_bytes": artifact["size_bytes"], "sha256": artifact["sha256"],
             "source_version": artifact["source_version"]})
    for layer_key in ("bronze_versions", "silver_versions", "gold_versions"):
        for dataset_id, version_id in (outputs.get(layer_key) or {}).items():
            version = repository.get_version(version_id)
            add(f"version:{version_id}", layer_key.split("_")[0], dataset_id,
                {"version_id": version_id, "rows": version["row_count"], "partition": version["partition"],
                 "versions": version["versions"], "is_current": version["is_current"]})
            for source in version["inputs"]:
                if source.get("type") == "artifact":
                    add(f"artifact:{source['id']}", "artifact", f"artifact #{source['id']}")
                    edges.append({"from": f"artifact:{source['id']}", "to": f"version:{version_id}"})
                elif source.get("type") == "dataset_version":
                    upstream = repository.get_version(source["id"])
                    add(f"version:{source['id']}", upstream["dataset_id"].split(".")[0], upstream["dataset_id"],
                        {"version_id": source["id"], "rows": upstream["row_count"]})
                    edges.append({"from": f"version:{source['id']}", "to": f"version:{version_id}"})
                elif source.get("type") == "entity_resolution":
                    add("er", "entity_resolution", f"Entity resolution {source['algorithm']}",
                        {"fingerprint": source.get("fingerprint")})
                    edges.append({"from": "er", "to": f"version:{version_id}"})
    if outputs.get("er_state"):
        for version_id in outputs["er_state"].get("silver_versions", []):
            if any(n["id"] == f"version:{version_id}" for n in nodes):
                edges.append({"from": f"version:{version_id}", "to": "er"})
    return {"nodes": nodes, "edges": [dict(t) for t in {tuple(e.items()) for e in edges}]}


# ---------------------------------------------------------------- artifacts & schedules

@router.get("/artifacts")
def list_artifacts(_: Viewer, source: str | None = None, limit: Annotated[int, Query(le=500)] = 100):
    return jsonable(repository.list_artifacts(source, limit))


@router.get("/schedules")
def list_schedules(_: Viewer):
    return jsonable(scheduler.list_schedules())


class ScheduleRequest(BaseModel):
    pipeline_definition_id: str
    name: str
    parameters: dict[str, Any] = {}
    cadence: str
    cron: str | None = None
    check_interval_minutes: int = 360


@router.post("/schedules", status_code=201)
def create_schedule(body: ScheduleRequest, _: Annotated[Principal, Depends(require(CONFIGURE_SOURCE))]):
    try:
        return jsonable(scheduler.upsert_schedule(body.pipeline_definition_id, body.name, body.parameters,
                                                  body.cadence, body.cron,
                                                  check_interval_minutes=body.check_interval_minutes))
    except (ParameterError, ValueError, KeyError) as error:
        raise HTTPException(422, str(error)) from None


@router.patch("/schedules/{schedule_id}")
def toggle_schedule(schedule_id: int, body: SourceSettings,
                    _: Annotated[Principal, Depends(require(CONFIGURE_SOURCE))]):
    with db.control() as conn:
        row = conn.execute("UPDATE dp.pipeline_schedules SET enabled = %s, updated_at = now() WHERE id = %s "
                           "RETURNING *", [body.enabled, schedule_id]).fetchone()
    if row is None:
        raise HTTPException(404, "Unknown schedule")
    return jsonable(row)


# ---------------------------------------------------------------- metrics

@router.get("/metrics", response_class=PlainTextResponse)
def prometheus_metrics():
    """Prometheus exposition built from the control plane (works across all worker processes)."""
    with db.control() as conn:
        runs = conn.execute("SELECT source_id, status, count(*) AS n FROM dp.pipeline_runs GROUP BY 1, 2").fetchall()
        totals = conn.execute("""
            SELECT r.source_id, s.stage, sum(s.records_out)::bigint AS records, sum(s.records_rejected)::bigint AS rejected,
                   sum(extract(epoch FROM s.finished_at - s.started_at)) AS seconds, count(*) AS n,
                   sum(greatest(s.attempt - 1, 0))::bigint AS retries
            FROM dp.pipeline_stage_runs s JOIN dp.pipeline_runs r ON r.id = s.run_id
            WHERE s.status = 'SUCCEEDED' GROUP BY 1, 2
        """).fetchall()
        downloaded = conn.execute("SELECT source_id, sum(bytes_downloaded)::bigint AS b FROM dp.pipeline_runs "
                                  "GROUP BY 1").fetchall()
        rates = conn.execute("""
            SELECT DISTINCT ON (r.source_id, s.stage) r.source_id, s.stage, (s.metrics->>'records_per_sec')::float AS rate
            FROM dp.pipeline_stage_runs s JOIN dp.pipeline_runs r ON r.id = s.run_id
            WHERE s.metrics ? 'records_per_sec' ORDER BY r.source_id, s.stage, s.finished_at DESC
        """).fetchall()
    lines = ["# TYPE pipeline_runs_total counter"]
    lines += [f'pipeline_runs_total{{source="{r["source_id"]}",status="{r["status"]}"}} {r["n"]}' for r in runs]
    lines.append("# TYPE pipeline_failures_total counter")
    lines += [f'pipeline_failures_total{{source="{r["source_id"]}"}} {r["n"]}' for r in runs if r["status"] == "FAILED"]
    lines.append("# TYPE records_processed_total counter")
    lines += [f'records_processed_total{{source="{t["source_id"]}",stage="{t["stage"]}"}} {t["records"] or 0}' for t in totals]
    lines.append("# TYPE records_rejected_total counter")
    lines += [f'records_rejected_total{{source="{t["source_id"]}",stage="{t["stage"]}"}} {t["rejected"] or 0}' for t in totals]
    lines.append("# TYPE stage_duration_seconds summary")
    for t in totals:
        lines.append(f'stage_duration_seconds_sum{{source="{t["source_id"]}",stage="{t["stage"]}"}} {t["seconds"] or 0}')
        lines.append(f'stage_duration_seconds_count{{source="{t["source_id"]}",stage="{t["stage"]}"}} {t["n"]}')
    lines.append("# TYPE stage_retries_total counter")
    lines += [f'stage_retries_total{{source="{t["source_id"]}",stage="{t["stage"]}"}} {t["retries"]}' for t in totals]
    lines.append("# TYPE bytes_downloaded_total counter")
    lines += [f'bytes_downloaded_total{{source="{d["source_id"]}"}} {d["b"] or 0}' for d in downloaded]
    lines.append("# TYPE processing_rate gauge")
    lines += [f'processing_rate{{source="{r["source_id"]}",stage="{r["stage"]}"}} {r["rate"]}' for r in rates]
    return "\n".join(lines) + "\n"
