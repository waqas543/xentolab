"""Writing dataset versions: staged, size-targeted Parquet files, atomically committed to the catalog.

A version is built in a private staging directory, moved into place with one rename,
and only then marked current in dp.dataset_versions. Readers therefore never see a
half-written version, and the previous version stays intact until the new one commits.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from .. import repository
from ..storage import ArtifactStorage

TARGET_FILE_BYTES = 256 * 1024 * 1024
ROW_GROUP_ROWS = 256 * 1024


def version_location(dataset_id: str, partition: dict, version_id: int) -> str:
    layer, name = dataset_id.split(".", 1)
    parts = "/".join(f"{k}={partition[k]}" for k in sorted(partition))
    return f"{layer}/{name}/{parts + '/' if parts else ''}v={version_id}"


def schema_fingerprint(schema: pa.Schema) -> tuple[dict, str]:
    fields = [{"name": f.name, "type": str(f.type), "nullable": f.nullable} for f in schema]
    digest = hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()[:16]
    return {"fields": fields}, digest


class DatasetVersionWriter:
    """Stream Arrow batches into a new dataset version.

        with DatasetVersionWriter(storage, "bronze.osm_nodes", {...}, schema, ...) as writer:
            writer.write_batch(batch)
        writer.version  # committed catalog row
    """

    def __init__(self, storage: ArtifactStorage, dataset_id: str, partition: dict, schema: pa.Schema, *,
                 run_id: int | None, versions: dict, inputs: list[dict],
                 target_file_bytes: int = TARGET_FILE_BYTES, compression: str = "zstd"):
        self.storage = storage
        self.dataset_id = dataset_id
        self.partition = partition
        self.schema = schema
        self.run_id = run_id
        self.versions = versions
        self.inputs = inputs
        self.target_file_bytes = target_file_bytes
        self.compression = compression
        self.version: dict | None = None
        self.row_count = 0
        self._writer: pq.ParquetWriter | None = None
        self._file_index = 0
        self._files: list[Path] = []

    def __enter__(self):
        self._row = repository.begin_version(self.dataset_id, self.partition, "pending", self.run_id,
                                             self.versions, self.inputs)
        self.staging_key, self.staging_path = self.storage.staging_directory()
        return self

    def _open_file(self):
        path = self.staging_path / f"part-{self._file_index:05d}.parquet"
        self._file_index += 1
        self._files.append(path)
        self._writer = pq.ParquetWriter(path, self.schema, compression=self.compression,
                                        compression_level=3 if self.compression == "zstd" else None,
                                        write_statistics=True, use_dictionary=True)

    def write_batch(self, batch: pa.RecordBatch | pa.Table) -> None:
        if batch.num_rows == 0:
            return
        if self._writer is None:
            self._open_file()
        table = batch if isinstance(batch, pa.Table) else pa.Table.from_batches([batch])
        self._writer.write_table(table, row_group_size=ROW_GROUP_ROWS)
        self.row_count += table.num_rows
        if self._files[-1].stat().st_size >= self.target_file_bytes:
            self._writer.close()
            self._writer = None

    def adopt_files(self, row_count: int) -> None:
        """Files were written directly into staging_path by another engine (e.g. DuckDB COPY)."""
        self._files = sorted(self.staging_path.glob("*.parquet"))
        self.row_count = row_count

    def __exit__(self, exc_type, exc, tb):
        if self._writer is not None:
            self._writer.close()
        if exc_type is not None:
            repository.fail_version(self._row["id"])
            self.storage.delete(self.staging_key)
            return False
        if not self._files:
            # An empty version is still a real, committed state (e.g. a region with no places).
            pq.write_table(self.schema.empty_table(), self.staging_path / "part-00000.parquet",
                           compression=self.compression)
            self._files = [self.staging_path / "part-00000.parquet"]

        location = version_location(self.dataset_id, self.partition, self._row["id"])
        size = sum(f.stat().st_size for f in self.staging_path.glob("*.parquet"))
        files = len(list(self.staging_path.glob("*.parquet")))
        try:
            self.storage.commit_directory(self.staging_key, location)
            schema, schema_hash = schema_fingerprint(self.schema)
            self.version = repository.commit_version(self._row["id"], file_count=files, row_count=self.row_count,
                                                     size_bytes=size, schema=schema, schema_hash=schema_hash,
                                                     location=location)
        except Exception:
            repository.fail_version(self._row["id"])
            if self.storage.exists(self.staging_key):
                self.storage.delete(self.staging_key)
            raise
        return False


def copy_query_into(writer: DatasetVersionWriter, con, sql: str, params: list | None = None) -> int:
    """Let DuckDB write a query result as size-targeted Parquet files into the writer's staging dir."""
    out = writer.staging_path / "_copy"
    con.execute(f"""
        COPY ({sql}) TO '{out}'
        (FORMAT parquet, COMPRESSION zstd, FILE_SIZE_BYTES {writer.target_file_bytes},
         ROW_GROUP_SIZE {ROW_GROUP_ROWS}, FILENAME_PATTERN 'part-{{i}}')
    """, params or [])
    rows = 0
    produced = sorted(out.glob("*.parquet")) if out.exists() else []
    for index, path in enumerate(produced):
        rows += pq.ParquetFile(path).metadata.num_rows
        path.rename(writer.staging_path / f"part-{index:05d}.parquet")
    if out.exists():
        out.rmdir()
    if not produced:
        # Keep the schema even when the result is empty.
        con.execute(f"COPY (SELECT * FROM ({sql}) LIMIT 0) TO '{writer.staging_path / 'part-00000.parquet'}' "
                    "(FORMAT parquet)", params or [])
    writer.schema = pq.read_schema(sorted(writer.staging_path.glob("*.parquet"))[0])
    writer.adopt_files(rows)
    return rows
