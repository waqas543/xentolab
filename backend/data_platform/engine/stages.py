"""Stage handlers: map each pipeline stage to adapter hooks or platform services.

Idempotency decisions are made from persisted facts (catalog versions, checkpoints,
serving state) rather than in-memory flags, so a retried or resumed run makes the
same decisions as the original one.
"""

from __future__ import annotations

from dataclasses import asdict

from .. import repository
from ..errors import PermanentError
from ..quality import runner as quality
from ..sources.base import DataSourceAdapter, StageResult
from ..states import Stage
from .context import StageContext


def _artifact(ctx: StageContext, adapter: DataSourceAdapter) -> dict:
    artifact_id = ctx.get("artifact_id") or ctx.params.get("artifact_id")
    if not artifact_id:
        latest = adapter.latest_artifact(ctx.params)  # backfill without an explicit artifact
        if latest is None:
            raise PermanentError("No artifact for this run (download did not run and none is stored)")
        artifact_id = latest["id"]
    artifact = repository.get_artifact(int(artifact_id))
    if artifact is None or artifact["status"] != "available":
        raise PermanentError(f"Artifact {artifact_id} is not available")
    if "artifact_id" not in ctx.state:
        ctx.put(artifact_id=artifact["id"])
    return artifact


def discover(adapter: DataSourceAdapter, ctx: StageContext) -> StageResult:
    datasets = adapter.discover_available_datasets(ctx)
    if not datasets:
        raise PermanentError("The provider offers no dataset matching these parameters")
    chosen = datasets[0]
    ctx.put(discovered=asdict(chosen))
    repository.update_run(ctx.run_id, records_discovered=len(datasets))
    return StageResult(records_out=len(datasets), metrics={"dataset": chosen.dataset_name,
                                                           "source_version": chosen.source_version})


def download(adapter: DataSourceAdapter, ctx: StageContext) -> StageResult:
    result = adapter.download(ctx)
    if not ctx.get("artifact_id"):
        raise PermanentError(f"Adapter {adapter.source_id} did not record an artifact")
    artifact = repository.get_artifact(ctx.get("artifact_id"))
    repository.update_run(ctx.run_id, records_downloaded=1,
                          bytes_downloaded=0 if result.skipped_reason else (artifact["size_bytes"] or 0))
    return result


def validate(adapter: DataSourceAdapter, ctx: StageContext) -> StageResult:
    return adapter.validate_download(_artifact(ctx, adapter), ctx)


def bronze_ingest(adapter: DataSourceAdapter, ctx: StageContext) -> StageResult:
    result = adapter.ingest_bronze(_artifact(ctx, adapter), ctx)
    repository.update_run(ctx.run_id, bronze_records=result.records_out or 0)
    return result


def _quality_stage(adapter: DataSourceAdapter, ctx: StageContext, state_key: str, layer: str,
                   platform_checks=None) -> StageResult:
    version_ids: dict[str, int] = ctx.get(state_key) or {}
    if not version_ids:
        return StageResult.skipped("No dataset versions were produced to check")
    definition = repository.get_definition(ctx.run["pipeline_definition_id"])
    overrides = definition["quality_overrides"] if definition else {}
    results = []
    checked = 0
    for dataset_id, version_id in version_ids.items():
        version = repository.get_version(version_id)
        if quality.already_checked(version_id, str(ctx.stage)):
            continue  # identical data already passed this gate in an earlier run
        checks = (platform_checks(dataset_id) if platform_checks else None) or adapter.quality_checks(layer, dataset_id)
        if not checks:
            continue
        checked += 1
        results += quality.run_checks(checks=checks, version=version, run_id=ctx.run_id,
                                      stage_run_id=ctx.stage_run_id, stage=str(ctx.stage), overrides=overrides)
        ctx.check_cancelled()
    if not checked:
        return StageResult.skipped("Quality already verified for these dataset versions")
    summary = quality.enforce(results)
    repository.increment_run(ctx.run_id, warning_count=summary["warned"])
    return StageResult(records_in=summary["checks"], records_out=summary["passed"], metrics=summary)


def bronze_quality(adapter, ctx):
    return _quality_stage(adapter, ctx, "bronze_versions", "bronze")


def silver_transform(adapter: DataSourceAdapter, ctx: StageContext) -> StageResult:
    result = adapter.transform_silver(ctx)
    repository.update_run(ctx.run_id, silver_records=result.records_out or 0)
    return result


def silver_quality(adapter, ctx):
    from ..quality.suites import silver_checks

    return _quality_stage(adapter, ctx, "silver_versions", "silver", silver_checks)


def entity_resolution(adapter, ctx):
    from ..resolution.service import resolve

    return resolve(ctx)


def gold_build(adapter: DataSourceAdapter, ctx: StageContext) -> StageResult:
    from ..gold.builder import build_gold

    result = build_gold(ctx)
    extra = adapter.build_gold(ctx)
    if extra is not None:
        result.metrics["source_specific"] = extra.metrics
    repository.update_run(ctx.run_id, gold_records=result.records_out or 0)
    return result


def gold_quality(adapter, ctx):
    from ..quality.suites import gold_checks

    return _quality_stage(adapter, ctx, "gold_versions", "gold", gold_checks)


def analytics_refresh(adapter: DataSourceAdapter, ctx: StageContext) -> StageResult:
    from ..gold.publish import publish

    result = publish(ctx)
    metrics = adapter.collect_metrics(ctx)
    if metrics:
        repository.record_metrics(ctx.run_id, str(ctx.stage), metrics, {"source": ctx.source_id})
        result.metrics["source_metrics"] = metrics
    return result


STAGE_HANDLERS = {
    Stage.DISCOVER: discover,
    Stage.DOWNLOAD: download,
    Stage.VALIDATE: validate,
    Stage.BRONZE_INGEST: bronze_ingest,
    Stage.BRONZE_QUALITY: bronze_quality,
    Stage.SILVER_TRANSFORM: silver_transform,
    Stage.SILVER_QUALITY: silver_quality,
    Stage.ENTITY_RESOLUTION: entity_resolution,
    Stage.GOLD_BUILD: gold_build,
    Stage.GOLD_QUALITY: gold_quality,
    Stage.ANALYTICS_REFRESH: analytics_refresh,
}
