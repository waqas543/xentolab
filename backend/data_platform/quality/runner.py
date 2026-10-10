"""Runs a quality suite against dataset versions and records the results."""

from __future__ import annotations

from .. import repository
from ..errors import QualityGateError
from ..lake.reader import duck, parquet_source, version_files
from ..observability import get_logger
from .checks import QualityCheck

log = get_logger(__name__)


def already_checked(dataset_version_id: int, stage: str) -> bool:
    """True when this exact version already *passed* this gate (a failed gate is always evaluated again)."""
    from .. import db

    with db.control() as conn:
        rows = conn.execute("""
            SELECT status, severity FROM dp.quality_results WHERE dataset_version_id = %s AND stage = %s
        """, [dataset_version_id, stage]).fetchall()
    return bool(rows) and not any(r["severity"] == "error" and r["status"] in ("failed", "error") for r in rows)


def input_sources(version: dict) -> list[str]:
    """Which sources fed a version (from its upstream dataset versions' partitions)."""
    from .. import db

    ids = [i["id"] for i in version.get("inputs") or [] if i.get("type") == "dataset_version"]
    if not ids:
        return []
    with db.control() as conn:
        rows = conn.execute("""
            SELECT DISTINCT partition->>'source' AS source FROM dp.dataset_versions
            WHERE id = ANY(%s) AND partition ? 'source'
        """, [ids]).fetchall()
    return sorted(r["source"] for r in rows)


def previous_version(version: dict) -> dict | None:
    from .. import db

    with db.control() as conn:
        return conn.execute("""
            SELECT * FROM dp.dataset_versions
            WHERE dataset_id = %s AND partition_key = %s AND status = 'committed' AND id < %s
            ORDER BY id DESC LIMIT 1
        """, [version["dataset_id"], version["partition_key"], version["id"]]).fetchone()


def check_context(version: dict) -> dict:
    """Context for checks that compare with the previous version of the same partition."""
    previous = previous_version(version)
    if previous is None:
        return {"previous_row_count": None}
    current_sources, previous_sources = input_sources(version), input_sources(previous)
    return {"previous_row_count": previous["row_count"],
            "composition_changed": current_sources != previous_sources,
            "sources": current_sources, "previous_sources": previous_sources}


def run_checks(*, checks: list[QualityCheck], version: dict, run_id: int, stage_run_id: int, stage: str,
               overrides: dict | None = None) -> list[dict]:
    """Evaluate checks on one dataset version. Returns stored result rows. Never raises for check failures."""
    overrides = (overrides or {}).get(version["dataset_id"], {})
    files = version_files(version)
    con = duck()
    source = parquet_source(files)
    context = check_context(version)
    results = []
    try:
        for check in checks:
            check = check.with_overrides(overrides.get(check.name, {}))
            try:
                outcome = check.evaluate(con, source, context)
                failed_pct = outcome.failure_percentage
                if outcome.records_failed == 0 or failed_pct <= check.threshold_pct:
                    status = "passed"
                else:
                    status = "failed" if check.severity == "error" else "warned"
                result = dict(records_checked=outcome.records_checked, records_failed=outcome.records_failed,
                              failure_percentage=round(failed_pct, 4), details=outcome.details, status=status)
            except Exception as error:  # a broken check is itself a failure, never silently passed
                result = dict(records_checked=None, records_failed=None, failure_percentage=None,
                              details={"error": f"{type(error).__name__}: {error}"}, status="error")
            results.append({
                "pipeline_run_id": run_id, "stage_run_id": stage_run_id, "stage": stage,
                "dataset_id": version["dataset_id"], "dataset_version_id": version["id"],
                "check_name": check.name, "check_type": check.check_type, "severity": check.severity,
                "threshold_percentage": check.threshold_pct, **result,
            })
    finally:
        con.close()
    repository.record_quality_results(results)
    return results


def enforce(results: list[dict]) -> dict:
    """Raise QualityGateError if any error-severity check failed; return a summary otherwise."""
    failed = [r for r in results if r["status"] in ("failed", "error") and r["severity"] == "error"]
    warned = [r for r in results if r["status"] == "warned" or (r["status"] == "error" and r["severity"] == "warn")]
    summary = {"checks": len(results), "passed": sum(r["status"] == "passed" for r in results),
               "warned": len(warned), "failed": len(failed)}
    if failed:
        names = ", ".join(f"{r['dataset_id']}:{r['check_name']} ({r['failure_percentage']}% failed)" for r in failed)
        raise QualityGateError(f"Quality gate failed: {names}", context=summary)
    return summary
