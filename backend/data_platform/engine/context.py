"""What a stage implementation sees: parameters, shared run state, storage, progress and cancellation."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .. import repository
from ..config import Settings
from ..errors import RunCancelled
from ..observability import get_logger
from ..states import Stage
from ..storage import ArtifactStorage

log = get_logger(__name__)


@dataclass
class StageContext:
    run: dict
    params: dict
    stage: Stage
    stage_run_id: int
    attempt: int
    storage: ArtifactStorage
    settings: Settings
    state: dict                                   # shared across stages, persisted to run.outputs
    _cleanups: list[Callable[[], None]] = field(default_factory=list)
    _last_progress_write: float = 0.0
    _last_cancel_check: float = 0.0
    _rate_anchor: tuple[float, int] | None = None
    _rate: float | None = None
    _rate_unit: str | None = None

    PROGRESS_INTERVAL = 1.0
    CANCEL_INTERVAL = 2.0

    @property
    def run_id(self) -> int:
        return self.run["id"]

    @property
    def source_id(self) -> str:
        return self.run["source_id"]

    # -- shared state -----------------------------------------------------------------

    def put(self, **values: Any) -> None:
        """Record outputs other stages (and retries/resumes) can read, e.g. artifact_id."""
        self.state.update(values)
        repository.merge_run_outputs(self.run_id, values)

    def get(self, key: str, default=None):
        return self.state.get(key, default)

    # -- progress & cancellation ------------------------------------------------------

    def check_cancelled(self, force: bool = False) -> None:
        now = time.monotonic()
        if force or now - self._last_cancel_check >= self.CANCEL_INTERVAL:
            self._last_cancel_check = now
            if repository.is_cancel_requested(self.run_id):
                raise RunCancelled()

    def progress(self, current: int, total: int | None = None, unit: str = "records",
                 message: str | None = None, force: bool = False) -> None:
        """Report progress (throttled). Also the cooperative cancellation point.

        `total` must be the real total or None — never a guess. The UI only shows a
        percentage when it is known.
        """
        now = time.monotonic()
        if self._rate_anchor is None or unit != self._rate_unit:
            # A new phase (e.g. "items" then "entities") starts its own rate.
            self._rate_anchor, self._rate, self._rate_unit = (now, current), None, unit
        if force or now - self._last_progress_write >= self.PROGRESS_INTERVAL:
            anchor_time, anchor_value = self._rate_anchor
            if now - anchor_time >= 1.0:
                instant = (current - anchor_value) / (now - anchor_time)
                self._rate = instant if self._rate is None else 0.6 * self._rate + 0.4 * instant
                self._rate_anchor = (now, current)
            self._last_progress_write = now
            repository.update_stage(
                self.stage_run_id,
                progress_current=current,
                progress_total=total,
                progress_unit=unit,
                progress_message=message,
                progress_updated_at=_utcnow(),
            )
            if self._rate is not None:
                repository.merge_stage_metrics(self.stage_run_id, {"rate_per_sec": round(self._rate, 1),
                                                                   "rate_unit": unit})
        self.check_cancelled()

    def on_cleanup(self, callback: Callable[[], None]) -> None:
        """Register cleanup for failure/cancellation (e.g. delete a staging directory)."""
        self._cleanups.append(callback)

    def run_cleanups(self) -> None:
        while self._cleanups:
            callback = self._cleanups.pop()
            try:
                callback()
            except Exception:
                log.exception("cleanup failed")

    def clear_cleanups(self) -> None:
        self._cleanups.clear()


def _utcnow():
    return repository.now()
