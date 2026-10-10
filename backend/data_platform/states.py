"""Explicit state machines for pipeline runs and stage runs.

All status values live here; nothing else in the codebase spells status strings.
"""

from enum import StrEnum

from .errors import InvalidTransition


class Stage(StrEnum):
    DISCOVER = "DISCOVER"
    DOWNLOAD = "DOWNLOAD"
    VALIDATE = "VALIDATE"
    BRONZE_INGEST = "BRONZE_INGEST"
    BRONZE_QUALITY = "BRONZE_QUALITY"
    SILVER_TRANSFORM = "SILVER_TRANSFORM"
    SILVER_QUALITY = "SILVER_QUALITY"
    ENTITY_RESOLUTION = "ENTITY_RESOLUTION"
    GOLD_BUILD = "GOLD_BUILD"
    GOLD_QUALITY = "GOLD_QUALITY"
    ANALYTICS_REFRESH = "ANALYTICS_REFRESH"


# Canonical order. Pipelines run a subsequence of this.
STAGE_ORDER: list[Stage] = list(Stage)


class RunStatus(StrEnum):
    PENDING = "PENDING"
    QUEUED = "QUEUED"
    DISCOVERING = "DISCOVERING"
    DOWNLOADING = "DOWNLOADING"
    VALIDATING = "VALIDATING"
    BRONZE_LOADING = "BRONZE_LOADING"
    SILVER_PROCESSING = "SILVER_PROCESSING"
    ENTITY_RESOLUTION = "ENTITY_RESOLUTION"
    GOLD_PROCESSING = "GOLD_PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    PARTIALLY_COMPLETED = "PARTIALLY_COMPLETED"

    @property
    def is_terminal(self) -> bool:
        return self in TERMINAL_RUN_STATUSES

    @property
    def is_executing(self) -> bool:
        return self in EXECUTING_RUN_STATUSES


class StageStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    RETRYING = "RETRYING"       # waiting for backoff before the next attempt
    SUCCEEDED = "SUCCEEDED"
    SKIPPED = "SKIPPED"         # idempotent no-op, or not applicable (e.g. backfill start)
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

    @property
    def is_terminal(self) -> bool:
        return self in (StageStatus.SUCCEEDED, StageStatus.SKIPPED, StageStatus.FAILED, StageStatus.CANCELLED)


TERMINAL_RUN_STATUSES = frozenset(
    {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.PARTIALLY_COMPLETED}
)

STAGE_RUN_STATUS: dict[Stage, RunStatus] = {
    Stage.DISCOVER: RunStatus.DISCOVERING,
    Stage.DOWNLOAD: RunStatus.DOWNLOADING,
    Stage.VALIDATE: RunStatus.VALIDATING,
    Stage.BRONZE_INGEST: RunStatus.BRONZE_LOADING,
    Stage.BRONZE_QUALITY: RunStatus.BRONZE_LOADING,
    Stage.SILVER_TRANSFORM: RunStatus.SILVER_PROCESSING,
    Stage.SILVER_QUALITY: RunStatus.SILVER_PROCESSING,
    Stage.ENTITY_RESOLUTION: RunStatus.ENTITY_RESOLUTION,
    Stage.GOLD_BUILD: RunStatus.GOLD_PROCESSING,
    Stage.GOLD_QUALITY: RunStatus.GOLD_PROCESSING,
    Stage.ANALYTICS_REFRESH: RunStatus.GOLD_PROCESSING,
}

EXECUTING_RUN_STATUSES = frozenset(set(STAGE_RUN_STATUS.values()))

_RUN_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.PENDING: frozenset({RunStatus.QUEUED, RunStatus.CANCELLED}),
    RunStatus.QUEUED: frozenset(EXECUTING_RUN_STATUSES | {RunStatus.CANCELLED, RunStatus.FAILED}),
}
for _status in EXECUTING_RUN_STATUSES:
    # Executing states may move forward to any later executing state (stages can be skipped),
    # stay in place (two stages share a status) or finish.
    _later = {s for s in EXECUTING_RUN_STATUSES if list(RunStatus).index(s) >= list(RunStatus).index(_status)}
    _RUN_TRANSITIONS[_status] = frozenset(_later | TERMINAL_RUN_STATUSES)
for _status in TERMINAL_RUN_STATUSES:
    _RUN_TRANSITIONS[_status] = frozenset()

_STAGE_TRANSITIONS: dict[StageStatus, frozenset[StageStatus]] = {
    StageStatus.PENDING: frozenset({StageStatus.RUNNING, StageStatus.SKIPPED, StageStatus.CANCELLED}),
    StageStatus.RUNNING: frozenset({StageStatus.SUCCEEDED, StageStatus.SKIPPED, StageStatus.FAILED,
                                    StageStatus.RETRYING, StageStatus.CANCELLED}),
    StageStatus.RETRYING: frozenset({StageStatus.RUNNING, StageStatus.CANCELLED, StageStatus.FAILED}),
    StageStatus.SUCCEEDED: frozenset(),
    StageStatus.SKIPPED: frozenset(),
    StageStatus.FAILED: frozenset(),
    StageStatus.CANCELLED: frozenset(),
}


def run_transition_allowed(current: RunStatus, target: RunStatus) -> bool:
    return target in _RUN_TRANSITIONS[current]


def stage_transition_allowed(current: StageStatus, target: StageStatus) -> bool:
    return target in _STAGE_TRANSITIONS[current]


def check_run_transition(current: RunStatus, target: RunStatus) -> None:
    if not run_transition_allowed(current, target):
        raise InvalidTransition(f"Run cannot move from {current} to {target}")


def check_stage_transition(current: StageStatus, target: StageStatus) -> None:
    if not stage_transition_allowed(current, target):
        raise InvalidTransition(f"Stage cannot move from {current} to {target}")


def allowed_run_sources(target: RunStatus) -> list[str]:
    """Statuses a run may be in for `target` to be legal (used in guarded UPDATEs)."""
    return [str(s) for s, targets in _RUN_TRANSITIONS.items() if target in targets]


def allowed_stage_sources(target: StageStatus) -> list[str]:
    return [str(s) for s, targets in _STAGE_TRANSITIONS.items() if target in targets]
