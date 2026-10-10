"""Building blocks shared by source adapters: artifact storage, verification, lineage, freshness checks.

Adapters stay small by composing these; nothing here knows about a specific provider.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .. import repository
from ..engine.context import StageContext
from ..errors import ValidationError
from ..storage import sha256_file


def stage_file(ctx: StageContext, filename: str) -> Path:
    """A path in a private staging directory that is removed if the stage fails or is cancelled."""
    staging_key, staging_path = ctx.storage.staging_directory()
    ctx.on_cleanup(lambda: ctx.storage.delete(staging_key) if ctx.storage.exists(staging_key) else None)
    ctx.state.setdefault("_staging_keys", []).append(staging_key)
    return staging_path / filename


def discard_staging(ctx: StageContext) -> None:
    """Delete the staging directories this stage created (after success; failures clean up automatically)."""
    for staging_key in ctx.state.pop("_staging_keys", []):
        if ctx.storage.exists(staging_key):
            ctx.storage.delete(staging_key)


def store_artifact(ctx: StageContext, artifact: dict, local: Path, **metadata) -> dict:
    """Move a finished local file into artifact storage (never overwriting) and mark the artifact available."""
    sha = sha256_file(local)
    key = f"bronze/{artifact['source_id']}/artifacts/{artifact['id']}/{local.name}"
    stored = ctx.storage.put_file(local, key, link=True)
    discard_staging(ctx)
    ctx.put(artifact_id=artifact["id"])
    return repository.update_artifact(artifact["id"], status="available", storage_key=key,
                                      size_bytes=stored.size_bytes, sha256=sha,
                                      download_completed_at=repository.now(),
                                      metadata={**artifact["metadata"], **metadata})


def reuse_artifact(ctx: StageContext, source_id: str, checksum: str):
    """An identical artifact already stored (same provider checksum), unless a download is forced."""
    if ctx.params.get("force_download"):
        return None
    existing = repository.find_available_artifact(source_id, source_checksum=checksum)
    if existing:
        ctx.put(artifact_id=existing["id"])
    return existing


def verify_artifact(ctx: StageContext, artifact: dict) -> Path:
    """The artifact's local path, after checking it still exists and its checksum is unchanged."""
    path = ctx.storage.local_path(artifact["storage_key"])
    if not path.exists():
        repository.update_artifact(artifact["id"], status="invalid")
        raise ValidationError(f"Artifact file missing: {artifact['storage_key']}")
    size = path.stat().st_size
    digest = sha256_file(path, on_progress=lambda done: ctx.progress(done, size, "bytes", "verifying checksum"))
    if digest != artifact["sha256"]:
        repository.update_artifact(artifact["id"], status="invalid")
        raise ValidationError("Stored artifact checksum changed since download")
    return path


def lineage_sql(ctx: StageContext, artifact: dict, source: str) -> str:
    """Lineage columns every Bronze record carries, as a DuckDB select-list fragment."""
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    return (f"'{source}' AS source, {int(artifact['id'])}::BIGINT AS artifact_id, "
            f"{int(ctx.run_id)}::BIGINT AS pipeline_run_id, TIMESTAMPTZ '{now}' AS ingested_at")


def current_bronze(dataset_id: str, artifact_id: int, parser_version: str) -> dict | None:
    """The current Bronze version for an artifact, if it was parsed by this parser version."""
    versions = repository.current_versions(dataset_id, {"artifact_id": artifact_id})
    if versions and versions[0]["versions"].get("parser") == parser_version:
        return versions[0]
    return None


def silver_is_current(ctx: StageContext, datasets: list[str], partition: dict, input_ids: set[int],
                      transformation_version: str, geography: list[int] | None = None) -> dict[str, int] | None:
    """Version ids of the current Silver datasets when they were built from exactly these inputs, else None."""
    if ctx.params.get("rebuild_silver"):
        return None
    current = {d: repository.current_versions(d, partition) for d in datasets}
    if not all(current.values()):
        return None
    first = current[datasets[0]][0]
    built_from = {i["id"] for i in first["inputs"]
                  if i.get("type") == "dataset_version" and str(i.get("dataset_id", "")).startswith("bronze.")}
    if built_from != input_ids or first["versions"].get("transformation") != transformation_version:
        return None
    if geography is not None and sorted(first["versions"].get("geography") or []) != sorted(geography):
        return None
    return {d: v[0]["id"] for d, v in current.items()}


def country_artifacts(source_id: str, country: str) -> list[dict]:
    """Available artifacts of a source for a country, newest first."""
    from .. import db

    with db.control() as conn:
        return conn.execute("""
            SELECT * FROM dp.dataset_artifacts WHERE source_id = %s AND country = %s AND status = 'available'
            ORDER BY coalesce(source_release_date, created_at) DESC, id DESC
        """, [source_id, country]).fetchall()
