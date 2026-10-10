"""Reading dataset versions with DuckDB."""

from __future__ import annotations

import threading
import weakref

import duckdb

from .. import repository
from ..config import get_settings
from ..storage import ArtifactStorage, get_storage


_active: dict[int, weakref.WeakSet] = {}
_active_lock = threading.Lock()


def interrupt_run_queries(run_id: int) -> int:
    """Interrupt every DuckDB query a run has open (thread-safe; used for cancellation)."""
    with _active_lock:
        connections = list(_active.get(run_id, ()))
    for con in connections:
        try:
            con.interrupt()
        except Exception:
            pass
    return len(connections)


def forget_run(run_id: int) -> None:
    with _active_lock:
        _active.pop(run_id, None)


def duck(threads: int | None = None, memory_limit: str | None = None) -> duckdb.DuckDBPyConnection:
    """A fresh in-memory DuckDB connection with spill-to-disk configured inside the data dir.

    Connections opened inside a pipeline run are registered so cancellation can interrupt them.
    """
    from ..observability import current_context

    settings = get_settings()
    temp_dir = settings.data_dir / "_tmp" / "duckdb"
    temp_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(config={"threads": threads or 4, "memory_limit": memory_limit or "2GB",
                                 "temp_directory": str(temp_dir), "preserve_insertion_order": False})
    run_id = current_context().get("pipeline_run_id")
    if run_id is not None:
        with _active_lock:
            _active.setdefault(run_id, weakref.WeakSet()).add(con)
    return con


def version_files(version: dict, storage: ArtifactStorage | None = None) -> list[str]:
    storage = storage or get_storage()
    base = storage.local_path(version["location"])
    return sorted(str(p) for p in base.glob("*.parquet"))


def dataset_files(dataset_id: str, partition: dict | None = None, storage: ArtifactStorage | None = None,
                  versions: list[dict] | None = None) -> list[str]:
    storage = storage or get_storage()
    versions = versions if versions is not None else repository.current_versions(dataset_id, partition)
    files: list[str] = []
    for version in versions:
        files.extend(version_files(version, storage))
    return files


def parquet_source(files: list[str]) -> str:
    """SQL table expression for a list of files (schema differences are unioned by column name)."""
    if not files:
        raise FileNotFoundError("No files for dataset")
    quoted = ", ".join("'" + f.replace("'", "''") + "'" for f in files)
    # hive_partitioning off: partition values come from the catalog, never from directory names
    # (DuckDB would otherwise invent columns such as `v` or override real ones such as `source`).
    return f"read_parquet([{quoted}], union_by_name = true, hive_partitioning = false)"
