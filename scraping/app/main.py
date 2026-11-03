"""Programmatic API behind the CLI: run, resume, validate and inspect jobs; test the loader."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from app.core.config import JobConfig, get_settings, load_job_config
from app.core.logging import setup_logging
from app.core.models import PageResponse
from app.core.state import StateStore
from app.fetch.fetcher import Fetcher
from app.jobs import RunStats, create_job
from app.parse.html_parser import HtmlDocument

RECORD_TYPES = ("search_results", "search_summaries", "domains", "contacts", "metadata", "profiles",
                "linkedin_companies", "enrichment_summary", "pages", "errors", "skipped")


def configure_logging(job_name: str | None = None, *, verbose: bool = False) -> None:
    settings = get_settings()
    log_file = settings.log_dir / f"{job_name}.log" if job_name else None
    setup_logging("DEBUG" if verbose else settings.log_level, log_file)


def run_job(job: str, *, resume: bool = False, restart: bool = False, limit: int | None = None,
            fetcher: Fetcher | None = None) -> RunStats:
    config = load_job_config(job)
    instance = create_job(config, fetcher=fetcher)
    return asyncio.run(instance.run(resume=resume, restart=restart, limit=limit))


def validate_input(job: str) -> dict[str, Any]:
    return create_job(load_job_config(job)).validate_input()


async def _load(config: JobConfig, url: str, render_js: bool) -> PageResponse:
    fetcher = Fetcher.from_config(config)
    try:
        return await fetcher.fetch(url, render_js=render_js)
    finally:
        await fetcher.aclose()


def test_loader(url: str, *, job: str = "fbr_company_enrichment", render_js: bool = True,
                loader: str | None = None) -> dict[str, Any]:
    config = load_job_config(job)
    if loader:
        config = config.model_copy(update={"loader": loader})
    page = asyncio.run(_load(config, url, render_js))
    doc = HtmlDocument(page.html, page.final_url)
    return {"page": page, "title": doc.title, "links": len(doc.links), "text_chars": len(doc.text)}


def output_files(config: JobConfig) -> dict[str, dict[str, Any]]:
    """Per record type: its files and total record count."""
    report: dict[str, dict[str, Any]] = {}
    if not config.output_dir.is_dir():
        return report
    for record_type in RECORD_TYPES:
        prefix = config.output_file_prefix(record_type)
        directory = config.output_dir / config.output_subdir(record_type)
        files = sorted(directory.glob(f"{prefix}_[0-9][0-9][0-9][0-9]*.jsonl"))
        if files:
            report[record_type] = {"files": [str(f.relative_to(config.output_dir)) for f in files],
                                   "records": sum(_count_lines(f) for f in files)}
    return report


def _count_lines(path: Path) -> int:
    with path.open("rb") as handle:
        return sum(chunk.count(b"\n") for chunk in iter(lambda: handle.read(1 << 20), b""))


def job_stats(job: str) -> dict[str, Any]:
    config = load_job_config(job)
    result: dict[str, Any] = {"job_name": config.job_name, "state_file": str(config.state_path),
                              "output_dir": str(config.output_dir), "items": {}, "runs": [], "top_errors": [],
                              "outputs": output_files(config)}
    if config.state_path.exists():
        state = StateStore(config.state_path)
        try:
            result.update(items=state.status_counts(), runs=state.runs(5), top_errors=state.top_errors())
        finally:
            state.close()
    return result
