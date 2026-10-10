"""OpenStreetMap source adapter: extracts (.osm.pbf) from Geofabrik or a local file."""

from __future__ import annotations

import hashlib
from pathlib import Path

import osmium

from ... import http, repository
from ...engine.context import StageContext
from ...errors import ParameterError, PermanentError, TransientError, ValidationError
from ...lake.reader import dataset_files, duck, parquet_source
from ...normalize import NORMALIZER_VERSION
from ...observability import get_logger
from ...quality.checks import RecordCountCheck, SchemaCheck, UniqueCheck
from ...silver_contract import geography_version_ids
from ...storage import sha256_file
from ...taxonomy import TAXONOMY_VERSION
from ..base import DataSourceAdapter, DiscoveredDataset, PipelineSpec, SourceCapabilities, StageResult
from . import bronze, geofabrik, silver

log = get_logger(__name__)

BRONZE_DATASETS = ("bronze.osm_nodes", "bronze.osm_ways", "bronze.osm_relations")


def read_header(path: Path) -> dict:
    try:
        reader = osmium.io.Reader(str(path), osmium.osm.osm_entity_bits.NOTHING)
    except Exception as error:
        raise ValidationError(f"Not a readable OSM file: {error}") from error
    try:
        header = reader.header()
        box = header.box()
        timestamp = header.get("osmosis_replication_timestamp") or None
        return {
            "bbox": [box.bottom_left.lon, box.bottom_left.lat, box.top_right.lon, box.top_right.lat]
            if box.valid() else None,
            "replication_timestamp": timestamp,
            "replication_sequence": header.get("osmosis_replication_sequence_number") or None,
            "replication_base_url": header.get("osmosis_replication_base_url") or None,
            "generator": header.get("generator") or None,
        }
    finally:
        reader.close()


class OSMAdapter(DataSourceAdapter):
    role = "Locations · boundaries"
    source_id = "osm"
    name = "OpenStreetMap"
    description = "Crowd-sourced map data: shops, offices, amenities, addresses, boundaries."
    homepage = "https://www.openstreetmap.org"
    license = "ODbL 1.0"
    capabilities = SourceCapabilities(supports_download=True, supports_incremental=False,
                                      supports_release_detection=True, provides_places=True,
                                      provides_geography=True)
    parser_version = "osm-bronze-1"
    # 3: places use the shared geography (GeoNames gazetteer when loaded)
    transformation_version = f"osm-silver-3+{NORMALIZER_VERSION}+{TAXONOMY_VERSION}"

    # -- declaration -------------------------------------------------------------------

    def pipelines(self) -> list[PipelineSpec]:
        return [PipelineSpec(
            id="osm.extract",
            name="OpenStreetMap extract",
            description="Discover, download and process a country/region extract into Bronze, Silver and Gold.",
            parameters={
                "country": {"type": "string", "pattern": "^[A-Za-z]{2}$", "title": "Country",
                            "description": "ISO 3166-1 alpha-2 code, e.g. PK. Also the Gold partition."},
                "region": {"type": ["string", "null"], "default": None, "title": "Geofabrik region",
                           "description": "Optional Geofabrik id (e.g. asia/pakistan). Derived from the country if empty."},
                "source_path": {"type": ["string", "null"], "default": None, "title": "Local .osm.pbf file",
                                "description": "Import a file already on this machine instead of downloading."},
            },
            required=["country"],
        )]

    def datasets(self) -> list[dict]:
        descriptions = {
            "bronze.osm_nodes": "Every OSM node: coordinates, tags, metadata.",
            "bronze.osm_ways": "Every OSM way with its ordered node references.",
            "bronze.osm_relations": "Every OSM relation with its members and roles.",
        }
        return [dict(dataset_id=d, layer="bronze", name=d.split(".", 1)[1].replace("_", " ").title(),
                     description=descriptions[d], source_id="osm", partition_columns=["artifact_id"])
                for d in BRONZE_DATASETS]

    def check_parameters(self, params: dict) -> dict:
        params["country"] = params["country"].upper()
        if params.get("source_path"):
            path = Path(params["source_path"]).expanduser()
            if not path.is_file():
                raise ParameterError(f"source_path does not exist: {path}")
            if not path.name.endswith((".osm.pbf", ".pbf", ".osm")):
                raise ParameterError("source_path must be an .osm.pbf file")
            params["source_path"] = str(path.resolve())
        return params

    def concurrency_key(self, params: dict) -> str:
        return f"osm:{params['country']}"

    def run_labels(self, params: dict):
        if params.get("source_path"):
            return params["country"], Path(params["source_path"]).name
        return params["country"], params.get("region") or "geofabrik"

    def latest_release(self, params: dict) -> DiscoveredDataset | None:
        if params.get("source_path"):
            return None
        return geofabrik.discover(params["country"], params.get("region"))

    def latest_artifact(self, params: dict) -> dict | None:
        from ... import db

        with db.control() as conn:
            return conn.execute("""
                SELECT * FROM dp.dataset_artifacts
                WHERE source_id = 'osm' AND status = 'available' AND country = %s
                ORDER BY coalesce(source_release_date, created_at) DESC, id DESC LIMIT 1
            """, [params["country"]]).fetchone()

    # -- discover / download / validate -------------------------------------------------

    def discover_available_datasets(self, ctx: StageContext) -> list[DiscoveredDataset]:
        params = ctx.params
        if params.get("source_path"):
            path = Path(params["source_path"])
            header = read_header(path)
            return [DiscoveredDataset(
                dataset_name=f"local:{path.name}", url=path.as_uri(),
                source_version=header["replication_timestamp"], release_date=header["replication_timestamp"],
                size_bytes=path.stat().st_size, country=params["country"], format="osm.pbf",
                metadata={"provider": "local", "path": str(path), **header},
            )]
        found = geofabrik.discover(params["country"], params.get("region"))
        return [found] if found else []

    def download(self, ctx: StageContext) -> StageResult:
        discovered = ctx.get("discovered")
        if not discovered:
            raise PermanentError("Nothing was discovered to download")
        if discovered["metadata"].get("provider") == "local":
            return self._import_local(ctx, discovered)
        return self._download_remote(ctx, discovered)

    def _register(self, ctx: StageContext, discovered: dict, *, filename: str, source_url: str) -> dict:
        return repository.create_artifact(
            source_id=self.source_id, dataset_name=discovered["dataset_name"], country=ctx.params["country"],
            region=discovered.get("region"), source_url=source_url, filename=filename, format="osm.pbf",
            compression="pbf", source_checksum=discovered.get("checksum"), source_version=discovered.get("source_version"),
            source_release_date=discovered.get("release_date"), download_started_at=repository.now(),
            pipeline_run_id=ctx.run_id, metadata=discovered.get("metadata") or {},
        )

    def _import_local(self, ctx: StageContext, discovered: dict) -> StageResult:
        path = Path(discovered["metadata"]["path"])
        size = path.stat().st_size
        sha = sha256_file(path, on_progress=lambda done: ctx.progress(done, size, "bytes", "hashing"))
        existing = repository.find_available_artifact(self.source_id, sha256=sha)
        if existing:
            ctx.put(artifact_id=existing["id"])
            return StageResult.skipped(f"Identical artifact already stored (#{existing['id']})", bytes_processed=size)

        artifact = self._register(ctx, discovered, filename=path.name, source_url=path.as_uri())
        key = f"bronze/osm/artifacts/{artifact['id']}/{path.name}"
        try:
            stored = ctx.storage.put_file(path, key, link=True)     # hard link: no extra disk on the same volume
        except Exception:
            repository.update_artifact(artifact["id"], status="failed")
            raise
        repository.update_artifact(artifact["id"], status="available", storage_key=key, size_bytes=stored.size_bytes,
                                   sha256=sha, download_completed_at=repository.now())
        ctx.put(artifact_id=artifact["id"])
        return StageResult(records_out=1, bytes_processed=stored.size_bytes, metrics={"mode": "local-import"})

    def _download_remote(self, ctx: StageContext, discovered: dict) -> StageResult:
        checksum = discovered.get("checksum")
        if checksum and not ctx.params["force_download"]:
            existing = repository.find_available_artifact(self.source_id, source_checksum=checksum)
            if existing:
                ctx.put(artifact_id=existing["id"])
                return StageResult.skipped(f"Provider checksum matches stored artifact #{existing['id']}")

        url = discovered["url"]
        filename = url.rsplit("/", 1)[-1]
        expected = discovered.get("size_bytes") or 0
        ctx.storage.ensure_free_space(expected, ctx.settings.min_free_disk_bytes)
        part_key = f"_downloads/osm/{hashlib.sha1(url.encode()).hexdigest()}.part"
        part = ctx.storage.local_path(part_key)

        artifact = self._register(ctx, discovered, filename=filename, source_url=url)
        try:
            result = http.download_to(url, part, on_progress=lambda done, total: ctx.progress(
                done, total, "bytes", f"downloading {filename}"))
        except Exception:
            repository.update_artifact(artifact["id"], status="failed")
            raise   # the .part file is kept so the next attempt resumes

        if checksum and checksum.startswith("md5:") and checksum[4:] != result.md5:
            part.unlink(missing_ok=True)
            repository.update_artifact(artifact["id"], status="invalid", metadata={**discovered["metadata"],
                                                                                   "md5": result.md5})
            raise TransientError("Downloaded file does not match the provider's md5 checksum; will re-download")

        existing = repository.find_available_artifact(self.source_id, sha256=result.sha256)
        if existing:
            part.unlink(missing_ok=True)
            repository.update_artifact(artifact["id"], status="deleted",
                                       metadata={**discovered["metadata"], "duplicate_of": existing["id"]})
            ctx.put(artifact_id=existing["id"])
            return StageResult.skipped(f"Downloaded content is identical to artifact #{existing['id']}",
                                       bytes_processed=result.size_bytes)

        key = f"bronze/osm/artifacts/{artifact['id']}/{filename}"
        ctx.storage.put_file(part, key, link=True)
        part.unlink(missing_ok=True)
        repository.update_artifact(artifact["id"], status="available", storage_key=key, size_bytes=result.size_bytes,
                                   sha256=result.sha256, download_completed_at=repository.now())
        ctx.put(artifact_id=artifact["id"])
        return StageResult(records_out=1, bytes_processed=result.size_bytes,
                           metrics={"mode": "download", "resumed_from_bytes": result.resumed_from})

    def validate_download(self, artifact: dict, ctx: StageContext) -> StageResult:
        path = ctx.storage.local_path(artifact["storage_key"])
        if not path.exists():
            repository.update_artifact(artifact["id"], status="invalid")
            raise ValidationError(f"Artifact file missing: {artifact['storage_key']}")
        size = path.stat().st_size
        sha = sha256_file(path, on_progress=lambda done: ctx.progress(done, size, "bytes", "verifying checksum"))
        if sha != artifact["sha256"]:
            repository.update_artifact(artifact["id"], status="invalid")
            raise ValidationError("Stored artifact checksum changed since download (corrupted on disk)")
        header = read_header(path)
        release = header["replication_timestamp"] or artifact["source_version"]
        repository.update_artifact(artifact["id"], source_version=release,
                                   source_release_date=release or artifact["source_release_date"],
                                   metadata={**artifact["metadata"], **header})

        scope = f"osm:{ctx.params['country']}"
        if header["replication_sequence"]:
            # Groundwork for incremental processing via OSM replication diffs.
            previous = repository.get_checkpoint(scope, "replication")
            if previous and int(previous["sequence"]) > int(header["replication_sequence"]):
                log.warning("artifact is older than the last processed replication state",
                            artifact_sequence=header["replication_sequence"], checkpoint=previous["sequence"])
            else:
                repository.set_checkpoint(scope, "replication", {
                    "sequence": int(header["replication_sequence"]), "timestamp": header["replication_timestamp"],
                    "base_url": header["replication_base_url"], "artifact_id": artifact["id"]}, ctx.run_id)
        return StageResult(records_in=1, records_out=1, bytes_processed=size, metrics={"header": header})

    # -- bronze / silver ----------------------------------------------------------------

    def _current_bronze(self, artifact_id: int) -> dict[str, dict] | None:
        found = {}
        for dataset_id in BRONZE_DATASETS:
            versions = repository.current_versions(dataset_id, {"artifact_id": artifact_id})
            if not versions or versions[0]["versions"].get("parser") != self.parser_version:
                return None
            found[dataset_id] = versions[0]
        return found

    def ingest_bronze(self, artifact: dict, ctx: StageContext) -> StageResult:
        existing = self._current_bronze(artifact["id"])
        if existing and not ctx.params["rebuild_bronze"]:
            ctx.put(bronze_versions={d: v["id"] for d, v in existing.items()})
            return StageResult.skipped(f"Bronze already exists for artifact #{artifact['id']}",
                                       records_out=sum(v["row_count"] for v in existing.values()))

        path = ctx.storage.local_path(artifact["storage_key"])
        # Bronze Parquet is typically 2-4x the PBF size.
        ctx.storage.ensure_free_space(4 * artifact["size_bytes"], ctx.settings.min_free_disk_bytes)
        versions = {"parser": self.parser_version, "pyosmium": getattr(osmium, "__version__", "4")}
        written = bronze.ingest(str(path), artifact, ctx, versions)
        ctx.put(bronze_versions={d: v["id"] for d, v in written.items()})
        counts = {d.split("_")[-1]: v["row_count"] for d, v in written.items()}
        return StageResult(records_in=sum(counts.values()), records_out=sum(counts.values()),
                           bytes_processed=artifact["size_bytes"],
                           metrics={**counts, "parquet_bytes": sum(v["size_bytes"] for v in written.values())})

    def transform_silver(self, ctx: StageContext) -> StageResult:
        artifact_id = ctx.get("artifact_id") or ctx.params.get("artifact_id")
        artifact = repository.get_artifact(artifact_id) if artifact_id else self.latest_artifact(ctx.params)
        if artifact is None:
            raise PermanentError("No OSM artifact available to build Silver from")
        bronze_versions = self._current_bronze(artifact["id"])
        if bronze_versions is None:
            raise PermanentError(f"Bronze is missing for artifact #{artifact['id']}; start from BRONZE_INGEST")

        country = ctx.params["country"]
        partition = {"source": "osm", "country": country}
        current = {d: repository.current_versions(d, partition)
                   for d in ("silver.places", "silver.rejects", "silver.geo_boundaries", "silver.geo_places")}
        bronze_ids = {v["id"] for v in bronze_versions.values()}
        if all(current.values()) and not ctx.params["rebuild_silver"]:
            places = current["silver.places"][0]
            input_ids = {i["id"] for i in places["inputs"]
                         if i.get("type") == "dataset_version" and i.get("dataset_id", "").startswith("bronze.")}
            same_geo = sorted(places["versions"].get("geography") or []) == geography_version_ids(country)
            if places["versions"].get("transformation") == self.transformation_version and input_ids == bronze_ids \
                    and same_geo:
                ctx.put(silver_versions={d: v[0]["id"] for d, v in current.items()})
                return StageResult.skipped("Silver is current for this Bronze, geography and transformation version",
                                           records_out=places["row_count"])

        written, stats = silver.transform(ctx, artifact, bronze_versions, country, self.transformation_version)
        ctx.put(silver_versions={d: v["id"] for d, v in written.items()})
        return StageResult(records_in=stats["candidates"], records_out=stats["places"],
                           records_rejected=stats["rejected"], metrics=stats)

    # -- quality & metrics --------------------------------------------------------------

    def quality_checks(self, layer: str, dataset_id: str):
        common = [UniqueCheck(name="unique_id", columns=("id",)),
                  RecordCountCheck(name="not_empty", min_rows=1)]
        if dataset_id == "bronze.osm_nodes":
            return common + [SchemaCheck(name="schema", required=(("id", "BIGINT"), ("latitude", "DOUBLE"),
                                                                  ("longitude", "DOUBLE"), ("tags", "MAP")))]
        if dataset_id == "bronze.osm_ways":
            return common + [SchemaCheck(name="schema", required=(("id", "BIGINT"), ("node_refs", "BIGINT[]"),
                                                                  ("tags", "MAP")))]
        if dataset_id == "bronze.osm_relations":
            # Small extracts can legitimately have no relations.
            return [UniqueCheck(name="unique_id", columns=("id",)),
                    SchemaCheck(name="schema", required=(("id", "BIGINT"), ("members", "STRUCT"), ("tags", "MAP")))]
        return []

    def collect_metrics(self, ctx: StageContext) -> dict[str, float]:
        partition = {"source": "osm", "country": ctx.params["country"]}
        files = dataset_files("silver.places", partition)
        metrics: dict[str, float] = {}
        for dataset_id in BRONZE_DATASETS:
            versions = repository.current_versions(dataset_id, {"artifact_id": ctx.get("artifact_id")}) \
                if ctx.get("artifact_id") else []
            if versions:
                metrics[f"{dataset_id}.rows"] = versions[0]["row_count"]
        if files:
            con = duck()
            try:
                row = con.execute(f"""
                    SELECT count(*), count(*) FILTER (WHERE has_name), count(normalized_domain),
                           count(normalized_phone), count(email), count(address),
                           count(DISTINCT normalized_domain) FILTER (WHERE NOT domain_is_platform)
                    FROM {parquet_source(files)}
                """).fetchone()
            finally:
                con.close()
            for name, value in zip(["places", "named", "with_domain", "with_phone", "with_email", "with_address",
                                    "unique_domains"], row):
                metrics[f"silver.places.{name}"] = value
        return metrics
