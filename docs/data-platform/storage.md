# Storage

Everything is local; there is no S3 and no paid service. All file access goes through `ArtifactStorage`
(`data_platform/storage.py`) using opaque relative keys. Code never builds filesystem paths itself.

```
$DATA_PLATFORM_DATA_DIR (default ./data)
├── bronze/
│   ├── osm/artifacts/<artifact_id>/<file>.osm.pbf      original artifact (hard-linked when imported locally)
│   ├── osm_nodes/artifact_id=<id>/v=<version_id>/part-00000.parquet
│   ├── osm_ways/…  osm_relations/…
├── silver/places/country=PK/source=osm/v=<version_id>/part-*.parquet
├── silver/rejects/…  silver/geo_boundaries/…
├── gold/businesses/country=PK/v=<version_id>/part-*.parquet
├── _staging/<uuid>/        versions being built (deleted on failure)
├── _downloads/             partial downloads (*.part), kept for resume
└── _tmp/duckdb/            DuckDB spill space
```

## Rules

- **Never overwrite.** `put_file`, `open_write` and `commit_directory` refuse to replace an existing key. Writes are
  atomic: temp file then rename, or staging directory then rename.
- **Checksums.** Every artifact gets a SHA-256, plus the provider checksum when one is published (Geofabrik `.md5`).
  The `(source, sha256)` pair is unique among available artifacts, so identical content is stored once.
- **Hard links.** Importing a local file (`source_path`) hard-links it into the store, which costs no extra disk on
  the same volume. Note that `du` counts a hard-linked file under each path.
- **Disk guard.** Download and Bronze check free space first, keeping a reserve (`DATA_PLATFORM_MIN_FREE_DISK_MB`,
  default 500). They fail with `InsufficientDiskError` instead of filling the disk.
- **No automatic deletion.** Old versions stay (`is_current = false`) for rollback and reproducibility. Pruning must
  be explicit (see [operations.md](operations.md)).

## Dataset versions: a minimal table format

`dp.dataset_versions` is the catalog. Each row is one immutable version of one partition of one dataset, with
location, file count, row count, size, schema and schema hash, code versions, input lineage and the producing run.
Writing a version works like this:

1. `begin_version` inserts the row as `building`.
2. Files are written into a private staging directory: PyArrow `DatasetVersionWriter` for streams, DuckDB `COPY` for
   set-based results. Files roll over at about 256 MB and use zstd compression with 256k-row row groups, so there are
   no swarms of tiny files.
3. One `rename` moves the directory to `…/v=<id>`.
4. `commit_version` flips `is_current` in one transaction. A partial unique index allows only one current version per
   (dataset, partition).

Readers resolve the current files for a dataset and partition filter from the catalog
(`lake.reader.dataset_files`) and read them with `read_parquet([...], union_by_name = true)`. That is snapshot
isolation: a reader never sees a half-written version, and schema differences between partitions (new columns) are
unioned rather than failing.

## Why not Iceberg (yet)

This catalog maps one-to-one onto Iceberg concepts: a version is a snapshot, a partition is a partition spec, and
`schema`/`schema_hash` correspond to schema evolution. Migrating means registering the existing Parquet files into an
Iceberg table, and the domain code would not change. It becomes worthwhile when multiple engines (Spark, Trino) write
concurrently, or when per-row deletes and CDC are needed.

## Serving copies

Gold is also loaded into PostgreSQL (schema `gold`, with PostGIS geometry, GIN and trigram indexes) for dashboards,
spatial queries and low-latency lookups. Each country partition is replaced in one serving transaction, and
`dp.serving_state` records which Gold version is live.

## Measured on Pakistan (2026-10-01 extract)

| | Size |
|---|---|
| Artifact (`.osm.pbf`) | 157 MB (hard link, no extra space) |
| Bronze Parquet (27.1M objects) | ~495 MB (nodes 433 MB, ways 62 MB, relations 0.8 MB) |
| Silver | 13 MB |
| Gold | 5 MB |

For comparison, the old `osm_raw` PostgreSQL tables for the same data use about 4 GB.
