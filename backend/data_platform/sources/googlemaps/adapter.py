"""Google Maps places source adapter: business listings collected from Google Maps searches, as JSON-lines files.

The listings are dropped as *.jsonl files in a folder (default data/google_map/), one place per line, and the folder
keeps growing: new files, or more lines appended to existing ones. Duplicates are handled at three levels:

1. Ingestion. Each run's artifact holds only the records no earlier run has stored. A record is identified by the
   hash of its canonical JSON, so re-running on the same files, a record found again by another search, or a file
   copied twice adds nothing. Artifacts are distinct slices that add up (like GDELT windows), and nothing is
   downloaded twice, so disk use grows with new data only.
2. Silver. All slices are folded into one row per Google place (place id, else CID / feature id), each field taking
   its newest non-empty value: a place scraped again with a new phone gets the new phone, and keeps the old website
   if the new scrape lacks one. The Silver id `googlemaps:<place id>` is stable, so the Gold id is too.
3. Entity resolution. Places are matched against OSM, Overture and every other source (name, distance, phone,
   website), so a business that Google and OSM both know becomes a single Gold business carrying both sources.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa

from ... import repository
from ...config import get_settings
from ...engine.context import StageContext
from ...errors import ParameterError, PermanentError, ValidationError
from ...lake.reader import dataset_files, duck, parquet_source
from ...lake.writer import DatasetVersionWriter
from ...normalize import NORMALIZER_VERSION
from ...observability import get_logger
from ...quality.checks import RecordCountCheck, SchemaCheck, UniqueCheck
from ...silver_contract import CONTRACT_VERSION, geography_version_ids
from ...taxonomy import GOOGLE_TAXONOMY_VERSION
from ..base import DataSourceAdapter, DiscoveredDataset, PipelineSpec, SourceCapabilities, StageResult
from ..common import reuse_artifact, silver_is_current, stage_file, store_artifact, verify_artifact
from . import silver

log = get_logger(__name__)

SOURCE = "googlemaps"
PIPELINE_ID = "googlemaps.places"
BRONZE_DATASET = "bronze.google_places"
DEFAULT_FOLDER = "google_map"
BATCH_ROWS = 20_000

# Provider fields kept as typed Bronze columns; the whole record is kept as well (`record`), so new fields survive.
TEXT_FIELDS = ("place_id", "cid", "feature_id", "result_type", "name", "category", "address", "phone", "website",
               "domain", "hours", "maps_url", "search_id", "search_tier", "search_query", "search_category",
               "search_category_group", "search_province", "search_city", "search_area")
BRONZE_SCHEMA = pa.schema(
    [("record_hash", pa.string()), ("line_number", pa.int64())]
    + [(name, pa.string()) for name in TEXT_FIELDS]
    + [("latitude", pa.float64()), ("longitude", pa.float64()), ("rating", pa.float64()), ("reviews", pa.int64()),
       ("sponsored", pa.bool_()), ("record", pa.string()),
       ("source", pa.string()), ("artifact_id", pa.int64()), ("pipeline_run_id", pa.int64()),
       ("ingested_at", pa.timestamp("s", tz="UTC"))])


def canonical(record: dict) -> tuple[str, str]:
    """(canonical JSON line, its hash). Key order and whitespace do not make a record new."""
    line = json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return line, hashlib.sha256(line.encode()).hexdigest()[:32]


def input_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    return sorted(p for p in path.rglob("*") if p.is_file() and p.name.endswith((".jsonl", ".jsonl.gz", ".ndjson")))


def read_records(path: Path):
    """(line number, record) for every complete JSON object line. A half-written last line (the scraper may still be
    appending) or any other unreadable line is skipped and counted; a later run picks it up once it is complete."""
    opener = gzip.open if path.name.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as handle:
        for number, raw in enumerate(handle, start=1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                record = json.loads(raw)
            except ValueError:
                yield number, None
                continue
            yield number, record if isinstance(record, dict) else None


def _text(value) -> str | None:
    if value is None or isinstance(value, (dict, list)):
        return None
    value = str(value).strip()
    return value or None


def _float(value) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _int(value) -> int | None:
    number = _float(value)
    return int(number) if number is not None else None


def _bool(value) -> bool | None:
    if isinstance(value, bool) or value is None:
        return value
    return str(value).strip().lower() in ("true", "1", "yes")


class GoogleMapsAdapter(DataSourceAdapter):
    role = "Locations"
    source_id = SOURCE
    name = "Google Maps"
    description = ("Business listings collected from Google Maps searches (JSON-lines files): category, address, "
                   "phone, website, rating, reviews and opening hours.")
    homepage = "https://maps.google.com"
    license = "Collected listings; check Google Maps terms before publishing"
    capabilities = SourceCapabilities(supports_download=True, provides_places=True)
    bronze_accumulates = True          # each artifact holds only records no earlier run stored
    parser_version = "googlemaps-bronze-1"
    transformation_version = (f"googlemaps-silver-2+{NORMALIZER_VERSION}+{GOOGLE_TAXONOMY_VERSION}"
                              f"+{CONTRACT_VERSION}")

    # -- declaration -------------------------------------------------------------------

    def pipelines(self) -> list[PipelineSpec]:
        return [PipelineSpec(
            id=PIPELINE_ID,
            name="Google Maps places",
            description="Ingest the Google Maps records not stored yet into Bronze, merge every record per place "
                        "into Silver, and match them with the other sources into Gold.",
            parameters={
                "country": {"type": "string", "pattern": "^[A-Za-z]{2}$", "title": "Country",
                            "description": "ISO 3166-1 alpha-2 code, e.g. PK. Also the Gold partition."},
                "source_path": {"type": ["string", "null"], "default": None, "title": "File or folder",
                                "description": "A .jsonl file, or a folder of them. Empty = data/google_map/."},
            },
            required=["country"],
        )]

    def datasets(self) -> list[dict]:
        return [dict(dataset_id=BRONZE_DATASET, layer="bronze", name="Google Maps places (raw)",
                     description="Google Maps records as collected, each stored once, with the full record.",
                     source_id=self.source_id, partition_columns=["country", "artifact_id"])]

    def check_parameters(self, params: dict) -> dict:
        params["country"] = params["country"].upper()
        path = Path(params["source_path"]).expanduser() if params.get("source_path") \
            else get_settings().data_dir / DEFAULT_FOLDER
        if not path.exists():
            raise ParameterError(f"source_path does not exist: {path}")
        params["source_path"] = str(path.resolve())
        return params

    def concurrency_key(self, params: dict) -> str:
        return f"{SOURCE}:{params['country']}"

    def run_labels(self, params: dict):
        return params["country"], Path(params["source_path"]).name if params.get("source_path") else DEFAULT_FOLDER

    # -- discover / download / validate -------------------------------------------------

    def discover_available_datasets(self, ctx: StageContext) -> list[DiscoveredDataset]:
        path = Path(ctx.params["source_path"])
        files = input_files(path)
        if not files:
            raise PermanentError(f"No .jsonl files in {path}")
        latest = max(f.stat().st_mtime for f in files)
        modified = datetime.fromtimestamp(latest, timezone.utc).replace(microsecond=0)
        return [DiscoveredDataset(
            dataset_name="places", url=path.as_uri(), source_version=modified.isoformat(),
            release_date=modified.date().isoformat(), size_bytes=sum(f.stat().st_size for f in files),
            country=ctx.params["country"], format="jsonl",
            metadata={"path": str(path), "files": [{"name": str(f.relative_to(path)) if f != path else f.name,
                                                    "bytes": f.stat().st_size} for f in files]})]

    def _stored_hashes(self, country: str) -> set[str]:
        """Hashes of every record already in Bronze for the country."""
        files = dataset_files(BRONZE_DATASET, {"country": country})
        if not files:
            return set()
        con = duck()
        try:
            return {row[0] for row in con.execute(f"SELECT DISTINCT record_hash FROM {parquet_source(files)}").fetchall()}
        finally:
            con.close()

    def download(self, ctx: StageContext) -> StageResult:
        discovered = ctx.get("discovered")
        if not discovered:
            raise PermanentError("Nothing was discovered to import")
        country, root = ctx.params["country"], Path(discovered["metadata"]["path"])
        stored = self._stored_hashes(country)
        filename = f"google-places-{country}.jsonl.gz"
        local = stage_file(ctx, filename)
        counts = {"read": 0, "new": 0, "already_stored": 0, "repeated_in_input": 0, "unreadable": 0}
        per_file, digest, seen = [], hashlib.sha256(), set()
        files = input_files(root)
        with gzip.open(local, "wt", encoding="utf-8") as out:
            for index, path in enumerate(files):
                new_here = 0
                for _, record in read_records(path):
                    if record is None:
                        counts["unreadable"] += 1
                        continue
                    counts["read"] += 1
                    line, record_hash = canonical(record)
                    if record_hash in stored:
                        counts["already_stored"] += 1
                    elif record_hash in seen:
                        counts["repeated_in_input"] += 1
                    else:
                        seen.add(record_hash)
                        digest.update(record_hash.encode())
                        out.write(line + "\n")
                        new_here += 1
                per_file.append({"name": path.name, "new_records": new_here})
                ctx.progress(index + 1, len(files), "files", f"{len(seen):,} new of {counts['read']:,} records")
        counts["new"] = len(seen)
        metrics = {**counts, "files": len(files)}

        if not seen:
            latest = self.latest_artifact(ctx.params)
            if latest is None:
                raise PermanentError(f"No readable records in {root}")
            ctx.put(artifact_id=latest["id"])
            return StageResult.skipped(f"No new records since artifact #{latest['id']} "
                                       f"({counts['already_stored']:,} already stored)", metrics=metrics)
        checksum = f"{SOURCE}:{country}:{digest.hexdigest()}"
        existing = reuse_artifact(ctx, self.source_id, checksum)
        if existing:   # same new records as an artifact whose Bronze never finished: ingest that one
            return StageResult.skipped(f"These {len(seen):,} records are already stored as artifact "
                                       f"#{existing['id']}", metrics=metrics)
        artifact = repository.create_artifact(
            source_id=self.source_id, dataset_name="places", country=country, source_url=discovered["url"],
            filename=filename, format="jsonl", compression="gzip", source_checksum=checksum,
            source_version=discovered["source_version"], source_release_date=discovered["release_date"],
            download_started_at=repository.now(), pipeline_run_id=ctx.run_id,
            metadata={"path": str(root), "files": per_file, **counts})
        stored_artifact = store_artifact(ctx, artifact, local, records=len(seen))
        if counts["unreadable"]:
            log.warning("Skipped unreadable Google Maps lines", count=counts["unreadable"])
        return StageResult(records_in=counts["read"], records_out=len(seen), records_rejected=counts["unreadable"],
                           bytes_processed=stored_artifact["size_bytes"], metrics=metrics)

    def validate_download(self, artifact: dict, ctx: StageContext) -> StageResult:
        path = verify_artifact(ctx, artifact)
        lines = 0
        try:
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                for raw in handle:
                    if not isinstance(json.loads(raw), dict):
                        raise ValidationError(f"Line {lines + 1} is not a JSON object")
                    lines += 1
        except (OSError, EOFError, ValueError) as error:
            raise ValidationError(f"Corrupt Google Maps artifact: {error}") from error
        if lines != artifact["metadata"].get("records", lines):
            raise ValidationError("Record count differs from the import")
        return StageResult(records_in=lines, records_out=lines)

    # -- bronze / silver ----------------------------------------------------------------

    def _current_bronze(self, artifact_id: int) -> dict | None:
        versions = repository.current_versions(BRONZE_DATASET, {"artifact_id": artifact_id})
        return next((v for v in versions if v["versions"].get("parser") == self.parser_version), None)

    def ingest_bronze(self, artifact: dict, ctx: StageContext) -> StageResult:
        existing = self._current_bronze(artifact["id"])
        if existing and not ctx.params["rebuild_bronze"]:
            ctx.put(bronze_versions={BRONZE_DATASET: existing["id"]})
            return StageResult.skipped(f"Bronze already exists for artifact #{artifact['id']}",
                                       records_out=existing["row_count"])
        path = ctx.storage.local_path(artifact["storage_key"])
        now = datetime.now(timezone.utc).replace(microsecond=0)
        total = artifact["metadata"].get("records")
        rows: list[dict] = []
        with DatasetVersionWriter(ctx.storage, BRONZE_DATASET,
                                  {"country": artifact["country"], "artifact_id": artifact["id"]}, BRONZE_SCHEMA,
                                  run_id=ctx.run_id, versions={"parser": self.parser_version},
                                  inputs=[{"type": "artifact", "id": artifact["id"],
                                           "sha256": artifact["sha256"]}]) as writer:
            for number, record in read_records(path):
                if record is None:
                    raise ValidationError(f"Line {number} of the artifact is not a JSON object")
                line, record_hash = canonical(record)
                rows.append({"record_hash": record_hash, "line_number": number,
                             **{name: _text(record.get(name)) for name in TEXT_FIELDS},
                             "latitude": _float(record.get("lat")), "longitude": _float(record.get("lng")),
                             "rating": _float(record.get("rating")), "reviews": _int(record.get("reviews")),
                             "sponsored": _bool(record.get("sponsored")), "record": line, "source": SOURCE,
                             "artifact_id": artifact["id"], "pipeline_run_id": ctx.run_id, "ingested_at": now})
                if len(rows) >= BATCH_ROWS:
                    writer.write_batch(pa.Table.from_pylist(rows, schema=BRONZE_SCHEMA))
                    rows = []
                    ctx.progress(writer.row_count, total, "records", "copying records")
            if rows:
                writer.write_batch(pa.Table.from_pylist(rows, schema=BRONZE_SCHEMA))
        ctx.put(bronze_versions={BRONZE_DATASET: writer.version["id"]})
        return StageResult(records_in=writer.row_count, records_out=writer.row_count,
                           bytes_processed=artifact["size_bytes"])

    def transform_silver(self, ctx: StageContext) -> StageResult:
        country = ctx.params["country"]
        bronze = [v for v in repository.current_versions(BRONZE_DATASET, {"country": country})
                  if v["versions"].get("parser") == self.parser_version]
        if not bronze:
            raise PermanentError(f"No Google Maps Bronze for {country}; start from DISCOVER")
        partition = {"source": SOURCE, "country": country}
        current = silver_is_current(ctx, ["silver.places", "silver.rejects"], partition, {v["id"] for v in bronze},
                                    self.transformation_version, geography=geography_version_ids(country))
        if current:
            ctx.put(silver_versions=current)
            return StageResult.skipped("Silver is current for this Bronze, geography and transformation version")
        written, stats = silver.transform(ctx, bronze, country, self.transformation_version)
        ctx.put(silver_versions={d: v["id"] for d, v in written.items()})
        return StageResult(records_in=stats["observations"], records_out=stats["places"],
                           records_rejected=stats["rejected"], metrics=stats)

    # -- quality & metrics --------------------------------------------------------------

    def quality_checks(self, layer: str, dataset_id: str):
        if dataset_id == BRONZE_DATASET:
            return [RecordCountCheck(name="not_empty", min_rows=1),
                    UniqueCheck(name="record_stored_once", columns=("record_hash",)),
                    SchemaCheck(name="schema", required=(("record_hash", "VARCHAR"), ("name", "VARCHAR"),
                                                         ("place_id", "VARCHAR"), ("record", "VARCHAR")))]
        return []

    def collect_metrics(self, ctx: StageContext) -> dict[str, float]:
        files = dataset_files("silver.places", {"source": SOURCE, "country": ctx.params["country"]})
        if not files:
            return {}
        con = duck()
        try:
            row = con.execute(f"""
                SELECT count(*), count(normalized_domain) FILTER (WHERE NOT domain_is_platform),
                       count(normalized_phone), count(address)
                FROM {parquet_source(files)}
            """).fetchone()
        finally:
            con.close()
        return dict(zip(["silver.places.places", "silver.places.with_domain", "silver.places.with_phone",
                         "silver.places.with_address"], [float(v or 0) for v in row]))
