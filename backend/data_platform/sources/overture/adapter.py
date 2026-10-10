"""Overture Maps source adapter (places theme).

Releases come from the official STAC catalog; only the requested country's row groups
are read from the public GeoParquet files. The artifact we keep is the country extract
(all provider columns), so Silver can always be rebuilt without contacting Overture.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pyarrow as pa
import pyarrow.parquet as pq

from ... import repository
from ...engine.context import StageContext
from ...errors import PermanentError, ValidationError
from ...lake.reader import dataset_files, duck, parquet_source
from ...lake.writer import DatasetVersionWriter
from ...normalize import NORMALIZER_VERSION
from ...observability import get_logger
from ...quality.checks import RecordCountCheck, SchemaCheck, UniqueCheck
from ...silver_contract import CONTRACT_VERSION, geography_version_ids
from ...storage import sha256_file
from ...taxonomy import TAXONOMY_VERSION
from ..base import DataSourceAdapter, DiscoveredDataset, PipelineSpec, SourceCapabilities, StageResult
from . import distribution, silver

log = get_logger(__name__)

BRONZE_DATASET = "bronze.overture_places"
REQUIRED_COLUMNS = ("id", "geometry", "names", "bbox", "addresses")
LINEAGE_FIELDS = [pa.field("source", pa.string()), pa.field("artifact_id", pa.int64()),
                  pa.field("pipeline_run_id", pa.int64()), pa.field("ingested_at", pa.timestamp("s", tz="UTC"))]


class OvertureAdapter(DataSourceAdapter):
    role = "Locations"
    source_id = "overture"
    name = "Overture Maps"
    description = "Open map data (Meta, Microsoft, Foursquare, OSM…) — places with phones, websites and confidence."
    homepage = "https://overturemaps.org"
    license = "CDLA-Permissive-2.0 (places)"
    capabilities = SourceCapabilities(supports_download=True, supports_incremental=False,
                                      supports_release_detection=True, provides_places=True)
    parser_version = "overture-bronze-1"
    transformation_version = f"overture-silver-2+{NORMALIZER_VERSION}+{TAXONOMY_VERSION}+{CONTRACT_VERSION}"

    # -- declaration -------------------------------------------------------------------

    def pipelines(self) -> list[PipelineSpec]:
        return [PipelineSpec(
            id="overture.places",
            name="Overture places",
            description="Extract one country's places from an Overture release into Bronze, Silver and Gold.",
            parameters={
                "country": {"type": "string", "pattern": "^[A-Za-z]{2}$", "title": "Country",
                            "description": "ISO 3166-1 alpha-2 code, e.g. PK. Places are selected by address country."},
                "release": {"type": ["string", "null"], "default": None, "title": "Release",
                            "description": "Overture release id (e.g. 2026-09-23.1). Empty = latest."},
            },
            required=["country"],
        )]

    def datasets(self) -> list[dict]:
        return [dict(dataset_id=BRONZE_DATASET, layer="bronze", name="Overture places",
                     description="Overture place records for a country, all provider columns preserved.",
                     source_id="overture", partition_columns=["artifact_id"])]

    def check_parameters(self, params: dict) -> dict:
        params["country"] = params["country"].upper()
        return params

    def concurrency_key(self, params: dict) -> str:
        return f"overture:{params['country']}"

    def run_labels(self, params: dict):
        return params["country"], f"places {params.get('release') or 'latest'}"

    def latest_release(self, params: dict) -> DiscoveredDataset | None:
        release = distribution.get_release(params.get("release"))
        return DiscoveredDataset(dataset_name="places", url=release.url, source_version=release.version,
                                 checksum=self._checksum(release.version, params["country"]))

    def latest_artifact(self, params: dict) -> dict | None:
        from ... import db

        with db.control() as conn:
            return conn.execute("""
                SELECT * FROM dp.dataset_artifacts WHERE source_id = 'overture' AND status = 'available' AND country = %s
                ORDER BY source_version DESC, id DESC LIMIT 1
            """, [params["country"]]).fetchone()

    @staticmethod
    def _checksum(release: str, country: str) -> str:
        return f"overture:{release}:places:{country}"

    # -- discover / download / validate -------------------------------------------------

    def discover_available_datasets(self, ctx: StageContext) -> list[DiscoveredDataset]:
        country = ctx.params["country"]
        release = distribution.get_release(ctx.params.get("release"))
        cache_scope = f"overture:{country}"
        cached = repository.get_checkpoint(cache_scope, f"bbox:{release.version}")
        bbox = cached or distribution.country_bbox(release, country)
        if not cached:
            repository.set_checkpoint(cache_scope, f"bbox:{release.version}", bbox, ctx.run_id)
        collection_json, collection_url = distribution.collection(release, "places", "place")
        items = distribution.items(collection_json, collection_url)
        margin = distribution.BBOX_MARGIN_DEGREES
        padded = [bbox[0] - margin, bbox[1] - margin, bbox[2] + margin, bbox[3] + margin]
        files = [item for item in items if not item.get("bbox") or distribution.intersects(item["bbox"], padded)]
        if not files:
            raise PermanentError(f"No Overture place files intersect {country}")
        return [DiscoveredDataset(
            dataset_name="places", url=collection_url, source_version=release.version,
            release_date=release.version.split(".")[0], checksum=self._checksum(release.version, country),
            country=country, format="geoparquet",
            size_bytes=None,
            metadata={"provider": "overture", "release": release.version, "country_bbox": bbox, "read_bbox": padded,
                      "collection_rows": collection_json.get("table:row_count"),
                      "files": [{"id": i["id"], "bbox": i.get("bbox"), "rows": i.get("properties", {}).get("num_rows")}
                                for i in files],
                      "selection": f"addresses[0].country = '{country}'"},
        )]

    def download(self, ctx: StageContext) -> StageResult:
        discovered = ctx.get("discovered")
        if not discovered:
            raise PermanentError("Nothing was discovered to download")
        country = ctx.params["country"]
        if not ctx.params["force_download"]:
            existing = repository.find_available_artifact(self.source_id, source_checksum=discovered["checksum"])
            if existing:
                ctx.put(artifact_id=existing["id"])
                return StageResult.skipped(f"Release {discovered['source_version']} for {country} already stored "
                                           f"(artifact #{existing['id']})")

        release = distribution.get_release(discovered["source_version"])
        collection_json, collection_url = distribution.collection(release, "places", "place")
        wanted = {f["id"] for f in discovered["metadata"]["files"]}
        items = [i for i in distribution.items(collection_json, collection_url) if i["id"] in wanted]
        tasks = distribution.plan_reads(items, discovered["metadata"]["read_bbox"])
        ctx.storage.ensure_free_space(sum(t.rows for t in tasks) * 150, ctx.settings.min_free_disk_bytes)

        filename = f"overture-places-{country}-{release.version}.parquet"
        artifact = repository.create_artifact(
            source_id=self.source_id, dataset_name="places", country=country, source_url=collection_url,
            filename=filename, format="geoparquet", compression="zstd", source_checksum=discovered["checksum"],
            source_version=release.version, source_release_date=discovered["release_date"],
            download_started_at=repository.now(), pipeline_run_id=ctx.run_id,
            metadata={**discovered["metadata"], "row_groups": len(tasks)},
        )
        staging_key, staging_path = ctx.storage.staging_directory()
        ctx.on_cleanup(lambda: ctx.storage.delete(staging_key) if ctx.storage.exists(staging_key) else None)
        local = staging_path / filename
        writer = None
        kept = 0
        scanned = {"rows": 0}

        def progress(done, total, rows):
            scanned["rows"] = rows
            ctx.progress(done, total, "row groups", f"{kept:,} {country} places from {rows:,} scanned rows")

        try:
            for table in distribution.read_country(tasks, country, progress):
                if table.num_rows == 0:
                    continue
                if writer is None:
                    writer = pq.ParquetWriter(local, table.schema, compression="zstd")
                writer.write_table(table)
                kept += table.num_rows
        except Exception:
            repository.update_artifact(artifact["id"], status="failed")
            raise
        finally:
            if writer is not None:
                writer.close()
        if kept == 0:
            repository.update_artifact(artifact["id"], status="failed")
            raise PermanentError(f"Overture release {release.version} has no places with address country {country}")

        sha = sha256_file(local)
        key = f"bronze/overture/artifacts/{artifact['id']}/{filename}"
        stored = ctx.storage.put_file(local, key, link=True)
        ctx.storage.delete(staging_key)
        repository.update_artifact(artifact["id"], status="available", storage_key=key, size_bytes=stored.size_bytes,
                                   sha256=sha, download_completed_at=repository.now(),
                                   metadata={**artifact["metadata"], "rows": kept, "rows_scanned": scanned["rows"]})
        ctx.put(artifact_id=artifact["id"])
        return StageResult(records_in=scanned["rows"], records_out=kept, bytes_processed=stored.size_bytes,
                           metrics={"row_groups": len(tasks), "rows_scanned": scanned["rows"], "places": kept})

    def validate_download(self, artifact: dict, ctx: StageContext) -> StageResult:
        path = ctx.storage.local_path(artifact["storage_key"])
        if not path.exists():
            repository.update_artifact(artifact["id"], status="invalid")
            raise ValidationError(f"Artifact file missing: {artifact['storage_key']}")
        size = path.stat().st_size
        if sha256_file(path, on_progress=lambda d: ctx.progress(d, size, "bytes", "verifying checksum")) != artifact["sha256"]:
            repository.update_artifact(artifact["id"], status="invalid")
            raise ValidationError("Stored artifact checksum changed since download")
        parquet = pq.ParquetFile(path)
        columns = set(parquet.schema_arrow.names)
        missing = [c for c in REQUIRED_COLUMNS if c not in columns]
        if missing:
            raise ValidationError(f"Overture schema changed: missing {', '.join(missing)}")
        known = set(REQUIRED_COLUMNS) | {"confidence", "websites", "emails", "socials", "phones", "brand", "sources",
                                         "operating_status", "basic_category", "taxonomy", "categories", "version"}
        new = sorted(columns - known)
        if new:
            log.warning("Overture release has new columns (kept in Bronze and Silver attributes)", columns=new)
        repository.update_artifact(artifact["id"], metadata={**artifact["metadata"],
                                                             "schema": [f"{f.name}: {f.type}" for f in parquet.schema_arrow]})
        return StageResult(records_in=parquet.metadata.num_rows, records_out=parquet.metadata.num_rows,
                           bytes_processed=size, metrics={"rows": parquet.metadata.num_rows, "new_columns": new})

    # -- bronze / silver ----------------------------------------------------------------

    def _current_bronze(self, artifact_id: int) -> dict | None:
        versions = repository.current_versions(BRONZE_DATASET, {"artifact_id": artifact_id})
        if versions and versions[0]["versions"].get("parser") == self.parser_version:
            return versions[0]
        return None

    def ingest_bronze(self, artifact: dict, ctx: StageContext) -> StageResult:
        existing = self._current_bronze(artifact["id"])
        if existing and not ctx.params["rebuild_bronze"]:
            ctx.put(bronze_versions={BRONZE_DATASET: existing["id"]})
            return StageResult.skipped(f"Bronze already exists for artifact #{artifact['id']}",
                                       records_out=existing["row_count"])
        parquet = pq.ParquetFile(ctx.storage.local_path(artifact["storage_key"]))
        schema = pa.schema(list(parquet.schema_arrow) + LINEAGE_FIELDS, metadata=parquet.schema_arrow.metadata)
        now = datetime.now(timezone.utc).replace(microsecond=0)
        total = parquet.metadata.num_rows
        done = 0
        with DatasetVersionWriter(ctx.storage, BRONZE_DATASET, {"artifact_id": artifact["id"]}, schema,
                                  run_id=ctx.run_id, versions={"parser": self.parser_version},
                                  inputs=[{"type": "artifact", "id": artifact["id"], "sha256": artifact["sha256"]}]) as writer:
            for batch in parquet.iter_batches(batch_size=50_000):
                n = batch.num_rows
                lineage = [pa.array(["overture"] * n), pa.array([artifact["id"]] * n, pa.int64()),
                           pa.array([ctx.run_id] * n, pa.int64()), pa.array([now] * n, pa.timestamp("s", tz="UTC"))]
                writer.write_batch(pa.RecordBatch.from_arrays(list(batch.columns) + lineage, schema=schema))
                done += n
                ctx.progress(done, total, "records", "copying provider records")
        ctx.put(bronze_versions={BRONZE_DATASET: writer.version["id"]})
        return StageResult(records_in=total, records_out=writer.row_count, bytes_processed=artifact["size_bytes"],
                           metrics={"parquet_bytes": writer.version["size_bytes"]})

    def transform_silver(self, ctx: StageContext) -> StageResult:
        artifact_id = ctx.get("artifact_id") or ctx.params.get("artifact_id")
        artifact = repository.get_artifact(artifact_id) if artifact_id else self.latest_artifact(ctx.params)
        if artifact is None:
            raise PermanentError("No Overture artifact available to build Silver from")
        bronze = self._current_bronze(artifact["id"])
        if bronze is None:
            raise PermanentError(f"Bronze is missing for artifact #{artifact['id']}; start from BRONZE_INGEST")

        country = ctx.params["country"]
        partition = {"source": "overture", "country": country}
        current = {d: repository.current_versions(d, partition) for d in ("silver.places", "silver.rejects")}
        if all(current.values()) and not ctx.params["rebuild_silver"]:
            places = current["silver.places"][0]
            geo_ids = geography_version_ids(country)
            same_inputs = {i["id"] for i in places["inputs"] if i.get("dataset_id") == BRONZE_DATASET} == {bronze["id"]}
            same_geo = sorted(places["versions"].get("geography") or []) == sorted(geo_ids)
            if same_inputs and same_geo and places["versions"].get("transformation") == self.transformation_version:
                ctx.put(silver_versions={d: v[0]["id"] for d, v in current.items()})
                return StageResult.skipped("Silver is current for this Bronze, geography and transformation version",
                                           records_out=places["row_count"])

        written, stats = silver.transform(ctx, artifact, bronze, country, self.transformation_version)
        ctx.put(silver_versions={d: v["id"] for d, v in written.items()})
        return StageResult(records_in=stats["candidates"], records_out=stats["places"],
                           records_rejected=stats["rejected"], metrics=stats)

    # -- quality & metrics --------------------------------------------------------------

    def quality_checks(self, layer: str, dataset_id: str):
        if dataset_id == BRONZE_DATASET:
            return [UniqueCheck(name="unique_id", columns=("id",)),
                    RecordCountCheck(name="not_empty", min_rows=1),
                    SchemaCheck(name="schema", required=(("id", "VARCHAR"), ("geometry", "BLOB|GEOMETRY"),
                                                         ("names", "STRUCT"), ("bbox", "STRUCT")))]
        return []

    def collect_metrics(self, ctx: StageContext) -> dict[str, float]:
        files = dataset_files("silver.places", {"source": "overture", "country": ctx.params["country"]})
        if not files:
            return {}
        con = duck()
        try:
            row = con.execute(f"""
                SELECT count(*), count(normalized_domain) FILTER (WHERE NOT domain_is_platform), count(normalized_phone),
                       count(email), avg(source_confidence), count(*) FILTER (WHERE source_confidence < 0.3)
                FROM {parquet_source(files)}
            """).fetchone()
        finally:
            con.close()
        return dict(zip(["silver.places.places", "silver.places.with_domain", "silver.places.with_phone",
                         "silver.places.with_email", "silver.places.avg_confidence",
                         "silver.places.low_confidence"], [float(v or 0) for v in row]))

