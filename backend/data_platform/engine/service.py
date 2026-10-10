"""Creating runs: parameter validation, backfill planning, enqueueing. Never executes work."""

from __future__ import annotations

from .. import repository
from ..errors import InvalidTransition, ParameterError
from ..gold.version import GOLD_VERSION
from ..sources.registry import find_pipeline
from ..states import RunStatus, Stage

RETRYABLE_STATUSES = {RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.PARTIALLY_COMPLETED}
ARTIFACT_STAGES = {Stage.DISCOVER, Stage.DOWNLOAD}


def start_run(definition_id: str, parameters: dict | None, *, trigger_type: str, triggered_by: str | None,
              schedule_id: int | None = None, retry_of_run_id: int | None = None) -> dict:
    adapter, pipeline = find_pipeline(definition_id)
    definition = repository.get_definition(definition_id)
    if definition is None:
        raise ParameterError(f"Pipeline {definition_id} is not registered; run `data-platform sync`")
    if not definition["enabled"]:
        raise ParameterError(f"Pipeline {definition_id} is disabled")
    source = repository.get_source(adapter.source_id)
    if not source["enabled"]:
        raise ParameterError(f"Source {adapter.source_id} is disabled")

    params = adapter.validate_parameters(pipeline, parameters or {})
    start = Stage(params["start_stage"])
    if params.get("artifact_id") is not None:
        artifact = repository.get_artifact(params["artifact_id"])
        if artifact is None or artifact["source_id"] != adapter.source_id or artifact["status"] != "available":
            raise ParameterError(f"Artifact {params['artifact_id']} is not an available {adapter.source_id} artifact")
        if start in ARTIFACT_STAGES:
            start = Stage.VALIDATE          # reuse the stored artifact: no discovery or download

    stages = []
    start_index = pipeline.stages.index(start) if start in pipeline.stages else 0
    for index, stage in enumerate(pipeline.stages):
        skip = None
        if index < start_index:
            skip = (f"Backfill from artifact {params['artifact_id']}" if params.get("artifact_id") is not None
                    else f"Backfill starting at {start}")
        stages.append((stage, adapter.stage_policy(stage).max_attempts, skip))

    if start != Stage.DISCOVER and trigger_type == "manual":
        trigger_type = "backfill"

    country, dataset = adapter.run_labels(params)
    return repository.create_run(
        definition_id=definition_id, source_id=adapter.source_id, parameters=params, trigger_type=trigger_type,
        triggered_by=triggered_by, concurrency_key=adapter.concurrency_key(params), country=country,
        dataset=dataset, stages=stages, retry_of_run_id=retry_of_run_id, schedule_id=schedule_id,
        versions={"parser": adapter.parser_version, "transformation": adapter.transformation_version,
                  "entity_resolution": params["er_algorithm"], "gold": GOLD_VERSION},
    )


def retry_run(run_id: int, triggered_by: str | None) -> dict:
    """A new run with the same parameters. Completed work is skipped by stage idempotency."""
    original = repository.get_run(run_id)
    if original is None:
        raise KeyError(run_id)
    if RunStatus(original["status"]) not in RETRYABLE_STATUSES:
        raise InvalidTransition(f"Only failed, cancelled or partially completed runs can be retried "
                                f"(run {run_id} is {original['status']})")
    params = dict(original["parameters"])
    return start_run(original["pipeline_definition_id"], params, trigger_type="retry", triggered_by=triggered_by,
                     retry_of_run_id=run_id)
