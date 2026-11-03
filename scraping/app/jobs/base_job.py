"""Job plugin interface, run lifecycle and registry.

`BaseJob.run()` owns everything generic: run IDs, resume/restart, state, fetcher, writers, stats and cleanup.
A concrete job implements `execute()` (and optionally `validate_input()`), registered with `@register_job`
under the name a job YAML refers to via `job_type` (or `job_name`).
"""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, ClassVar, TypeVar

from app.core.config import JobConfig, Settings, get_settings, load_extraction_rules
from app.core.exceptions import ConfigError
from app.core.logging import get_logger
from app.core.models import BaseRecord, ErrorRecord, RecordStatus
from app.core.state import StateStore
from app.core.utils import new_job_id, stable_id, utcnow
from app.fetch.fetcher import Fetcher
from app.parse import ParseContext, create_parsers
from app.writer import OutputManager

log = get_logger(__name__)


@dataclass
class RunStats:
    job_id: str
    job_name: str
    started_at: str = field(default_factory=lambda: utcnow().isoformat())
    finished_at: str | None = None
    status: str = "running"
    items: Counter[str] = field(default_factory=Counter)
    records: dict[str, int] = field(default_factory=dict)
    fetch: dict[str, int] = field(default_factory=dict)
    files: list[str] = field(default_factory=list)
    duration_s: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {"job_id": self.job_id, "job_name": self.job_name, "started_at": self.started_at,
                "finished_at": self.finished_at, "status": self.status, "items": dict(self.items),
                "records": self.records, "fetch": self.fetch, "files": self.files, "duration_s": self.duration_s}


@dataclass
class RunContext:
    job_id: str
    state: StateStore
    output: OutputManager
    fetcher: Fetcher
    stats: RunStats
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    extra_fetchers: list[Fetcher] = field(default_factory=list)


class BaseJob(ABC):
    job_type: ClassVar[str] = ""

    def __init__(self, config: JobConfig, settings: Settings | None = None, *, fetcher: Fetcher | None = None):
        self.config = config
        self.settings = settings or get_settings()
        self.rules = load_extraction_rules(config.extraction)
        self.parsers = create_parsers(config.parsers, ParseContext(rules=self.rules, config=config))
        self._fetcher = fetcher

    # ------------------------------------------------------------ lifecycle

    async def run(self, *, resume: bool = False, restart: bool = False, limit: int | None = None) -> RunStats:
        state = StateStore(self.config.state_path)
        if restart:
            state.reset()
        job_id = self._job_id(state, resume)
        fetcher = self._fetcher or Fetcher.from_config(self.config, self.settings)
        output = OutputManager.from_config(self.config)
        ctx = RunContext(job_id=job_id, state=state, output=output, fetcher=fetcher,
                         stats=RunStats(job_id=job_id, job_name=self.config.job_name))
        state.start_run(job_id, self.config.job_name)
        log.info("job.start", job=self.config.job_name, job_id=job_id, resume=resume, restart=restart,
                 loader=fetcher.loader_name, limit=limit)
        started = time.perf_counter()
        try:
            await self.execute(ctx, limit=limit)
            ctx.stats.status = "completed"
        except (KeyboardInterrupt, asyncio.CancelledError):
            ctx.stats.status = "interrupted"
            raise
        except BaseException:
            ctx.stats.status = "failed"
            raise
        finally:
            output.close()
            fetchers = [fetcher, *ctx.extra_fetchers]
            for each in fetchers:
                await each.aclose()
            ctx.stats.finished_at = utcnow().isoformat()
            ctx.stats.duration_s = round(time.perf_counter() - started, 2)
            ctx.stats.records = dict(output.counts)
            ctx.stats.fetch = dict(sum((Counter(each.stats.as_dict()) for each in fetchers), Counter()))
            ctx.stats.files = [str(p.relative_to(self.config.output_dir)) for p in output.files()]
            state.finish_run(job_id, ctx.stats.status, ctx.stats.as_dict())
            state.close()
            log.info("job.end", job=self.config.job_name, job_id=job_id, status=ctx.stats.status,
                     duration_s=ctx.stats.duration_s, **{f"items_{k}": v for k, v in ctx.stats.items.items()})
        return ctx.stats

    def _job_id(self, state: StateStore, resume: bool) -> str:
        if not resume:
            return new_job_id(self.config.job_name)
        latest = state.latest_run()
        if latest is None:
            raise ConfigError(f"nothing to resume: job {self.config.job_name!r} has never run")
        return latest["job_id"]

    def fetcher_for(self, ctx: RunContext, config: JobConfig) -> Fetcher:
        """A fetcher for a derived config (e.g. a search engine with its own loader and rate limit), closed with
        the run. The run's own fetcher when nothing differs, or when one was injected (tests)."""
        if self._fetcher is not None or config is self.config:
            return ctx.fetcher
        fetcher = Fetcher.from_config(config, self.settings)
        ctx.extra_fetchers.append(fetcher)
        return fetcher

    @abstractmethod
    async def execute(self, ctx: RunContext, *, limit: int | None = None) -> None:
        """Process the job's work items, writing records through ctx.output and committing to ctx.state."""

    def validate_input(self) -> dict[str, Any]:
        """Dry-run the input stage without fetching anything. Jobs without file input have nothing to check."""
        return {"note": f"job type {self.config.type!r} has no input file to validate"}

    # ------------------------------------------------------------ record helpers

    def base_fields(self, ctx: RunContext, record_type: str, dedup_key: str, **extra: Any) -> dict[str, Any]:
        return {"record_id": stable_id(self.config.job_name, record_type, dedup_key), "source": self.config.source,
                "job_id": ctx.job_id, "job_name": self.config.job_name, "loader": ctx.fetcher.loader_name, **extra}

    def error_record(self, ctx: RunContext, *, stage: str, error: BaseException, url: str | None = None,
                     attempts: int | None = None, key: str | None = None, error_type: str | None = None,
                     **extra: Any) -> ErrorRecord:
        dedup = f"{stage}|{key}|{url}|{utcnow().isoformat()}"
        return ErrorRecord(**self.base_fields(ctx, "errors", dedup, **extra), stage=stage,
                           error_type=error_type or type(error).__name__, message=str(error)[:2000], url=url,
                           attempts=attempts if attempts is not None else getattr(error, "attempts", None))

    def commit(self, ctx: RunContext, key: str, status: RecordStatus, records: list[BaseRecord], *,
               seen: list[tuple[str, str]], row_number: int | None = None, error: str | None = None) -> None:
        """Write a unit of work's records, make them durable, then mark it done (in that order)."""
        ctx.output.write_all(records)
        ctx.output.flush()
        ctx.state.commit_item(key, status, ctx.job_id, row_number=row_number, error=error, seen=seen)
        ctx.stats.items[str(status)] += 1


JOBS: dict[str, type[BaseJob]] = {}
J = TypeVar("J", bound=type[BaseJob])


def register_job(name: str):
    def decorator(cls: J) -> J:
        cls.job_type = name
        JOBS[name] = cls
        return cls
    return decorator


def create_job(config: JobConfig, settings: Settings | None = None, *, fetcher: Fetcher | None = None) -> BaseJob:
    if config.type not in JOBS:
        raise ConfigError(f"unknown job type {config.type!r}; available: {', '.join(sorted(JOBS))}")
    return JOBS[config.type](config, settings, fetcher=fetcher)

