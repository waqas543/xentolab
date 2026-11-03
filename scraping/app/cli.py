"""Command line interface.

    python -m app.cli run-job --job fbr_company_enrichment [--limit 10] [--restart]
    python -m app.cli resume --job fbr_company_enrichment
    python -m app.cli validate-input --job fbr_company_enrichment
    python -m app.cli test-loader --url https://example.com
    python -m app.cli stats --job fbr_company_enrichment
    python -m app.cli list-jobs
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import orjson
import typer
from rich.console import Console
from rich.table import Table

from app import main
from app.core.config import list_jobs, load_job_config
from app.core.exceptions import ScrapingError
from app.jobs import JOBS, RunStats

app = typer.Typer(help="Xento scraping engine", no_args_is_help=True, add_completion=False)
console = Console()

JobOption = Annotated[str, typer.Option("--job", "-j", help="Job name (config/jobs/<name>.yaml) or YAML path")]
Verbose = Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")]


def _fail(exc: Exception) -> None:
    console.print(f"[bold red]error:[/] {exc}")
    raise typer.Exit(code=2)


def _print_run(stats: RunStats) -> None:
    table = Table(title=f"{stats.job_name} · {stats.job_id}", show_header=False)
    table.add_row("status", stats.status)
    table.add_row("duration", f"{stats.duration_s}s")
    for key, value in sorted(stats.items.items()):
        table.add_row(f"items · {key}", str(value))
    for key, value in sorted(stats.records.items()):
        table.add_row(f"records · {key}", str(value))
    fetch = stats.fetch
    table.add_row("requests", f"{fetch.get('requests', 0)} ({fetch.get('failed', 0)} failed, "
                              f"{fetch.get('retries', 0)} retries, {fetch.get('cache_hits', 0)} cache hits)")
    console.print(table)


def _run(job: str, *, resume: bool, restart: bool, limit: int | None, verbose: bool) -> None:
    try:
        config = load_job_config(job)
        main.configure_logging(config.job_name, verbose=verbose)
        stats = main.run_job(job, resume=resume, restart=restart, limit=limit)
    except KeyboardInterrupt:
        console.print(f"[yellow]Interrupted.[/] Progress is saved; continue with: "
                      f"python -m app.cli resume --job {job}")
        raise typer.Exit(code=130) from None
    except ScrapingError as exc:
        _fail(exc)
    else:
        _print_run(stats)


@app.command("run-job")
def run_job(job: JobOption, limit: Annotated[int | None, typer.Option(help="Process at most N new items")] = None,
            restart: Annotated[bool, typer.Option(help="Forget all progress and start from scratch")] = False,
            yes: Annotated[bool, typer.Option("--yes", "-y", help="Don't ask before --restart")] = False,
            verbose: Verbose = False) -> None:
    """Run a job. Already completed items are skipped, so this is safe to re-run."""
    if restart and not yes:
        typer.confirm("--restart forgets all progress (output files are kept). Continue?", abort=True)
    _run(job, resume=False, restart=restart, limit=limit, verbose=verbose)


@app.command()
def resume(job: JobOption, limit: Annotated[int | None, typer.Option()] = None, verbose: Verbose = False) -> None:
    """Continue the latest run (same job_id): skips finished items, retries failed ones."""
    _run(job, resume=True, restart=False, limit=limit, verbose=verbose)


@app.command("validate-input")
def validate_input(job: JobOption) -> None:
    """Check the input file and name resolution without fetching anything."""
    try:
        report = main.validate_input(job)
    except ScrapingError as exc:
        _fail(exc)
        return
    console.print_json(orjson.dumps(report, default=str).decode())


@app.command("test-loader")
def test_loader(url: Annotated[str, typer.Option("--url", "-u")],
                job: Annotated[str, typer.Option("--job", "-j", help="Job whose loader settings to use")] =
                "fbr_company_enrichment",
                loader: Annotated[str | None, typer.Option(
                    help="Override: load_page | scrape_api | http | playwright")] = None,
                render_js: Annotated[bool, typer.Option("--render-js/--no-render-js")] = True,
                save: Annotated[Path | None, typer.Option(help="Write the HTML to this file")] = None,
                verbose: Verbose = False) -> None:
    """Fetch one URL through the configured loader and report status, latency, retries and final URL."""
    main.configure_logging(verbose=verbose)
    try:
        result: dict[str, Any] = main.test_loader(url, job=job, render_js=render_js, loader=loader)
    except ScrapingError as exc:
        _fail(exc)
        return
    page = result["page"]
    table = Table(show_header=False)
    for key, value in [("loader", page.loader), ("url", page.url), ("final_url", page.final_url),
                       ("status", page.status_code), ("latency", f"{page.latency_ms} ms"), ("attempts", page.attempts),
                       ("render_js", page.render_js), ("bytes", page.size_bytes), ("title", result["title"]),
                       ("links", result["links"]), ("text chars", result["text_chars"])]:
        table.add_row(key, str(value))
    console.print(table)
    if save:
        save.write_text(page.html, encoding="utf-8")
        console.print(f"saved HTML to {save}")


@app.command()
def stats(job: JobOption) -> None:
    """Progress, recent runs, top errors and output files for a job."""
    try:
        report = main.job_stats(job)
    except ScrapingError as exc:
        _fail(exc)
        return
    items = Table(title=f"{report['job_name']} items")
    items.add_column("status")
    items.add_column("count", justify="right")
    for status, count in sorted(report["items"].items()):
        items.add_row(status, str(count))
    console.print(items)

    outputs = Table(title="output")
    for column in ("record type", "records", "files"):
        outputs.add_column(column)
    for record_type, info in report["outputs"].items():
        files = info["files"]
        outputs.add_row(record_type, str(info["records"]),
                        f"{files[0]} … {files[-1]}" if len(files) > 1 else files[0])
    console.print(outputs)

    if report["runs"]:
        runs = Table(title="recent runs")
        for column in ("job_id", "started", "status", "items"):
            runs.add_column(column)
        for run in report["runs"]:
            runs.add_row(run["job_id"], run["started_at"][:19], run["status"],
                         ", ".join(f"{k}={v}" for k, v in (run["stats"].get("items") or {}).items()))
        console.print(runs)
    for error, count in report["top_errors"]:
        console.print(f"[red]{count}×[/] {error}")


@app.command("list-jobs")
def list_jobs_command() -> None:
    """Job configs in config/jobs and registered job types."""
    console.print("job configs:", ", ".join(list_jobs()) or "none")
    console.print("job types:  ", ", ".join(sorted(JOBS)))


if __name__ == "__main__":
    app()
