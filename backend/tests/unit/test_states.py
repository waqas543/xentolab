import pytest

from data_platform.errors import InvalidTransition
from data_platform.states import (
    STAGE_ORDER,
    STAGE_RUN_STATUS,
    RunStatus,
    Stage,
    StageStatus,
    allowed_run_sources,
    check_run_transition,
    check_stage_transition,
    run_transition_allowed,
)


def test_every_stage_maps_to_an_executing_run_status():
    assert set(STAGE_RUN_STATUS) == set(Stage)
    assert all(status.is_executing for status in STAGE_RUN_STATUS.values())


def test_happy_path_is_allowed():
    path = [RunStatus.PENDING, RunStatus.QUEUED] + [STAGE_RUN_STATUS[s] for s in STAGE_ORDER] + [RunStatus.COMPLETED]
    for current, target in zip(path, path[1:]):
        check_run_transition(current, target)


def test_stages_can_be_skipped_forward_but_not_backward():
    assert run_transition_allowed(RunStatus.DISCOVERING, RunStatus.SILVER_PROCESSING)
    assert not run_transition_allowed(RunStatus.SILVER_PROCESSING, RunStatus.DOWNLOADING)


@pytest.mark.parametrize("terminal", [RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED,
                                      RunStatus.PARTIALLY_COMPLETED])
def test_terminal_states_are_final(terminal):
    assert terminal.is_terminal
    for target in RunStatus:
        with pytest.raises(InvalidTransition):
            check_run_transition(terminal, target)


def test_queued_cannot_complete_without_running():
    with pytest.raises(InvalidTransition):
        check_run_transition(RunStatus.QUEUED, RunStatus.COMPLETED)


def test_cancellation_allowed_from_pending_queued_and_executing():
    for status in [RunStatus.PENDING, RunStatus.QUEUED, RunStatus.DOWNLOADING, RunStatus.GOLD_PROCESSING]:
        assert run_transition_allowed(status, RunStatus.CANCELLED)


def test_allowed_sources_used_by_guarded_updates():
    sources = allowed_run_sources(RunStatus.COMPLETED)
    assert "GOLD_PROCESSING" in sources and "QUEUED" not in sources


def test_stage_retry_cycle():
    check_stage_transition(StageStatus.PENDING, StageStatus.RUNNING)
    check_stage_transition(StageStatus.RUNNING, StageStatus.RETRYING)
    check_stage_transition(StageStatus.RETRYING, StageStatus.RUNNING)
    check_stage_transition(StageStatus.RUNNING, StageStatus.SUCCEEDED)
    with pytest.raises(InvalidTransition):
        check_stage_transition(StageStatus.SUCCEEDED, StageStatus.RUNNING)
    with pytest.raises(InvalidTransition):
        check_stage_transition(StageStatus.PENDING, StageStatus.SUCCEEDED)
