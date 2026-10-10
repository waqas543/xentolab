"""Worker process: claims queued runs and executes them; also drives the scheduler and recovers stale runs."""

from __future__ import annotations

import logging
import signal
import threading
import time

from .. import repository
from ..config import get_settings
from ..observability import WORKER_ID, RunLogHandler, configure_logging, get_logger
from . import scheduler
from .runner import execute_run

log = get_logger(__name__)


class Worker:
    def __init__(self):
        self.settings = get_settings()
        self.stop = threading.Event()
        self.current_run_id: int | None = None
        self._log_handler = RunLogHandler(repository.write_logs)

    def install_signal_handlers(self):
        def handle(signum, _frame):
            log.info("shutdown requested", signal=signum)
            self.stop.set()
            if self.current_run_id is not None:
                # Cooperative: the running stage stops at its next progress/cancellation check.
                try:
                    repository.request_cancel(self.current_run_id, f"worker-shutdown:{WORKER_ID}")
                except Exception:
                    log.exception("could not request cancellation on shutdown")

        signal.signal(signal.SIGTERM, handle)
        signal.signal(signal.SIGINT, handle)

    def run_once(self) -> bool:
        """Housekeeping plus at most one run. Returns True if a run was executed."""
        recovered = repository.recover_stale_runs(self.settings.stale_run_seconds)
        if recovered:
            log.warning("marked stale runs as failed", runs=recovered)
        scheduler.tick()
        run = repository.claim_next_run(WORKER_ID)
        if run is None:
            return False
        self.current_run_id = run["id"]
        try:
            execute_run(run)
        finally:
            self.current_run_id = None
            self._log_handler.flush()
        return True

    def serve(self) -> None:
        configure_logging()
        logging.getLogger().addHandler(self._log_handler)
        log.info("worker started", worker=WORKER_ID)
        while not self.stop.is_set():
            try:
                if not self.run_once():
                    self.stop.wait(self.settings.worker_poll_seconds)
            except Exception:
                log.exception("worker loop error")
                time.sleep(5)
        log.info("worker stopped")
