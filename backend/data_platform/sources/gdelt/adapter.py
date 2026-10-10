"""GDELT source adapter: organizations named in news about a country (discovery / popularity signal).

GDELT 2.0 publishes a Global Knowledge Graph file every 15 minutes (English and machine-
translated feeds). Each run reads the files since the previous run (a checkpoint; the
first run reads the last `hours`), keeps the records that mention a place in the
country or come from an outlet under its ccTLD, and stores only those records — the
provider lines unchanged, prefixed with feed and file name. Silver aggregates mentions per
organization across all stored windows. GDELT is a signal, never an authoritative registry:
it enriches Gold organizations but does not create them.
"""

from __future__ import annotations

import gzip
import io
import os
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from ... import http, repository
from ...engine.context import StageContext
from ...errors import PermanentError, ValidationError
from ...lake.reader import dataset_files, duck, parquet_source
from ...lake.writer import DatasetVersionWriter, copy_query_into
from ...normalize import NORMALIZER_VERSION
from ...observability import get_logger
from ...quality.checks import RecordCountCheck, SchemaCheck
from ..base import DataSourceAdapter, DiscoveredDataset, PipelineSpec, SourceCapabilities, StageResult
from ..common import current_bronze, reuse_artifact, stage_file, store_artifact, verify_artifact
from . import silver

log = get_logger(__name__)

BRONZE_DATASET = "bronze.gdelt_gkg"
STEP = timedelta(minutes=15)
FEEDS = {"english": "gkg.csv.zip", "translation": "translation.gkg.csv.zip"}
# GKG 2.1 columns, in file order.
GKG_COLUMNS = [
    "gkg_record_id", "date", "source_collection", "source_name", "document_url", "counts", "counts_v21",
    "themes", "enhanced_themes", "locations", "enhanced_locations", "persons", "enhanced_persons",
    "organizations", "enhanced_organizations", "tone", "enhanced_dates", "gcam", "sharing_image",
    "related_images", "social_image_embeds", "social_video_embeds", "quotations", "all_names", "amounts",
    "translation_info", "extras_xml",
]
LOCATIONS, ENHANCED_LOCATIONS, SOURCE_NAME = GKG_COLUMNS.index("locations"), GKG_COLUMNS.index("enhanced_locations"), 3


def base_url() -> str:
    return os.environ.get("GDELT_BASE_URL", "https://data.gdeltproject.org/gdeltv2").rstrip("/")


def timestamp(value: datetime) -> str:
    return value.strftime("%Y%m%d%H%M%S")


def parse_timestamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)


def latest_file_time() -> datetime:
    for raw in http.get_text(f"{base_url()}/lastupdate.txt").split("\n"):
        if raw.strip().endswith(".gkg.csv.zip"):
            return parse_timestamp(raw.strip().rsplit("/", 1)[1][:14])
    raise PermanentError("GDELT lastupdate.txt lists no GKG file")


def fips_code(country: str) -> str:
    """GDELT locations use FIPS 10-4 country codes (e.g. Germany = GM); GeoNames' countryInfo maps them."""
    cached = repository.get_checkpoint("gdelt", "fips")
    if not cached:
        geonames = os.environ.get("GEONAMES_BASE_URL", "https://download.geonames.org/export").rstrip("/")
        cached = {}
        for raw in http.get_text(f"{geonames}/dump/countryInfo.txt").split("\n"):
            if raw and not raw.startswith("#"):
                parts = raw.split("\t")
                if len(parts) > 3 and parts[3]:
                    cached[parts[0]] = parts[3]
        repository.set_checkpoint("gdelt", "fips", cached, None)
    return cached.get(country, country)


class RecordFilter:
    """Keeps GKG records about a country: a location in it, or an outlet under its ccTLD."""

    def __init__(self, fips: str, tld: str):
        self.marker = f"#{fips}#"
        self.fips = fips
        self.tld_suffix = f".{tld}"

    def keep(self, columns: list[str]) -> bool:
        if len(columns) < len(GKG_COLUMNS):
            return False
        if columns[SOURCE_NAME].endswith(self.tld_suffix):
            return True
        for field in (columns[LOCATIONS], columns[ENHANCED_LOCATIONS]):
            if self.marker in field:
                for location in field.split(";"):
                    parts = location.split("#")
                    if len(parts) > 2 and parts[2] == self.fips:
                        return True
        return False


def fetch_records(url: str, feed: str, keep: RecordFilter, session) -> tuple[list[str], int, bool]:
    """(kept lines prefixed with feed and file, lines read, file existed)."""
    try:
        body = http.request("GET", url, session=session, attempts=4).content
    except PermanentError as error:
        if error.context.get("status") == 404:
            return [], 0, False                  # GDELT occasionally skips an interval
        raise
    name = url.rsplit("/", 1)[1]
    kept, read = [], 0
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        with archive.open(archive.namelist()[0]) as handle:
            for raw in io.TextIOWrapper(handle, encoding="utf-8", errors="replace"):
                read += 1
                line = raw.rstrip("\n").rstrip("\r")
                if keep.keep(line.split("\t")):
                    kept.append(f"{feed}\t{name}\t{line}\n")
    return kept, read, True


class GDELTAdapter(DataSourceAdapter):
    role = "News signals"
    bronze_accumulates = True
    source_id = "gdelt"
    name = "GDELT"
    description = "Global news monitoring: which organizations the news about a country mentions, and how often."
    homepage = "https://www.gdeltproject.org"
    license = "Free for any use with attribution"
    capabilities = SourceCapabilities(supports_download=True, supports_incremental=True,
                                      supports_release_detection=True, provides_organizations=True)
    parser_version = "gdelt-bronze-1"
    transformation_version = f"gdelt-silver-2+{NORMALIZER_VERSION}"   # 2: country names are not organizations

    def pipelines(self) -> list[PipelineSpec]:
        return [PipelineSpec(
            id="gdelt.mentions",
            name="GDELT news mentions",
            description="Read new GDELT knowledge-graph files and aggregate the organizations mentioned in news "
                        "about one country.",
            parameters={
                "country": {"type": "string", "pattern": "^[A-Za-z]{2}$", "title": "Country",
                            "description": "ISO 3166-1 alpha-2 code, e.g. PK."},
                "hours": {"type": "integer", "default": 24, "minimum": 1, "maximum": 168, "title": "Window (hours)",
                          "description": "First run: how far back to start. Later runs continue from the last "
                                         "file read, at most this many hours per run (~1.7 GB of files per day "
                                         "is streamed; only matching records are stored)."},
                "include_translation": {"type": "boolean", "default": True, "title": "Translated news",
                                        "description": "Also read GDELT's machine-translated feed (Urdu and "
                                                       "other languages)."},
                "retention_days": {"type": "integer", "default": 90, "minimum": 1, "maximum": 3650,
                                   "title": "Silver window (days)",
                                   "description": "Mentions older than this are left out of the aggregates."},
                "parallel_requests": {"type": "integer", "default": 4, "minimum": 1, "maximum": 8,
                                      "title": "Parallel downloads"},
            },
            required=["country"],
        )]

    def datasets(self) -> list[dict]:
        return [dict(dataset_id=BRONZE_DATASET, layer="bronze", name="GDELT knowledge-graph records",
                     description="GKG 2.1 records about the country (all provider columns).",
                     source_id="gdelt", partition_columns=["country", "artifact_id"])]

    def check_parameters(self, params: dict) -> dict:
        params["country"] = params["country"].upper()
        return params

    def concurrency_key(self, params: dict) -> str:
        return f"gdelt:{params['country']}"

    def run_labels(self, params: dict):
        return params["country"], "news mentions"

    def latest_release(self, params: dict) -> DiscoveredDataset | None:
        latest = latest_file_time()
        return DiscoveredDataset(dataset_name="gkg", url=None, source_version=timestamp(latest),
                                 checksum=f"gdelt:latest:{timestamp(latest)}")

    # -- discover / download / validate -------------------------------------------------

    def discover_available_datasets(self, ctx: StageContext) -> list[DiscoveredDataset]:
        country, hours = ctx.params["country"], ctx.params["hours"]
        latest = latest_file_time()
        checkpoint = repository.get_checkpoint(f"gdelt:{country}", "last_file")
        start = parse_timestamp(checkpoint) + STEP if checkpoint else latest - timedelta(hours=hours) + STEP
        end = min(latest, start + timedelta(hours=hours) - STEP)
        feeds = ["english"] + (["translation"] if ctx.params["include_translation"] else [])
        slots = []
        moment = start
        while moment <= end:
            slots.append(timestamp(moment))
            moment += STEP
        return [DiscoveredDataset(
            dataset_name="gkg", url=base_url(), source_version=timestamp(end) if slots else checkpoint,
            release_date=end.date().isoformat(), country=country, format="tsv.gz",
            checksum=f"gdelt:gkg:{country}:{slots[0] if slots else '-'}:{slots[-1] if slots else '-'}:{'+'.join(feeds)}",
            metadata={"from": slots[0] if slots else None, "to": slots[-1] if slots else None, "slots": len(slots),
                      "feeds": feeds, "files": [f"{s}.{FEEDS[f]}" for s in slots for f in feeds],
                      "latest_available": timestamp(latest), "resumed_from_checkpoint": checkpoint},
        )]

    def download(self, ctx: StageContext) -> StageResult:
        discovered = ctx.get("discovered")
        country, meta = ctx.params["country"], discovered["metadata"]
        if not meta["files"]:
            latest = self.latest_artifact(ctx.params)
            if latest is None:
                raise PermanentError("No new GDELT files and nothing stored yet")
            ctx.put(artifact_id=latest["id"])
            return StageResult.skipped(f"No new GDELT files since {meta['resumed_from_checkpoint']}")
        existing = reuse_artifact(ctx, self.source_id, discovered["checksum"])
        if existing:
            return StageResult.skipped(f"Window {meta['from']}..{meta['to']} already stored "
                                       f"(artifact #{existing['id']})")
        keep = RecordFilter(fips_code(country), country.lower())
        filename = f"gdelt-gkg-{country}-{meta['from']}-{meta['to']}.tsv.gz"
        artifact = repository.create_artifact(
            source_id=self.source_id, dataset_name="gkg", country=country, source_url=discovered["url"],
            filename=filename, format="tsv", compression="gzip", source_checksum=discovered["checksum"],
            source_version=discovered["source_version"], source_release_date=discovered["release_date"],
            download_started_at=repository.now(), pipeline_run_id=ctx.run_id,
            metadata={k: v for k, v in meta.items() if k != "files"} | {"fips": keep.fips})
        session = http.new_session()
        tasks = [(f"{base_url()}/{name}", "translation" if ".translation." in name else "english")
                 for name in meta["files"]]
        local = stage_file(ctx, filename)
        done = kept = read = missing = 0

        def fetch(task):
            ctx.check_cancelled()
            return fetch_records(task[0], task[1], keep, session)

        try:
            with gzip.open(local, "wt", encoding="utf-8") as out, \
                    ThreadPoolExecutor(ctx.params["parallel_requests"]) as pool:
                for lines, count, existed in pool.map(fetch, tasks):
                    out.writelines(lines)
                    done += 1
                    kept += len(lines)
                    read += count
                    missing += 0 if existed else 1
                    ctx.progress(done, len(tasks), "files", f"{kept:,} of {read:,} records about {country}")
        except Exception:
            repository.update_artifact(artifact["id"], status="failed")
            raise
        if missing == len(tasks):
            repository.update_artifact(artifact["id"], status="failed")
            raise PermanentError("None of the GDELT files in the window exist")
        stored = store_artifact(ctx, artifact, local, records=kept, records_read=read, missing_files=missing)
        return StageResult(records_in=read, records_out=kept, bytes_processed=stored["size_bytes"],
                           metrics={"files": len(tasks), "missing_files": missing, "records_read": read,
                                    "records_kept": kept})

    def validate_download(self, artifact: dict, ctx: StageContext) -> StageResult:
        path = verify_artifact(ctx, artifact)
        lines = 0
        try:
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                for raw in handle:
                    if raw.count("\t") != len(GKG_COLUMNS) + 1:
                        raise ValidationError(f"Line {lines + 1} has {raw.count(chr(9)) + 1} columns, expected "
                                              f"{len(GKG_COLUMNS) + 2}")
                    lines += 1
        except (OSError, EOFError) as error:
            raise ValidationError(f"Corrupt GDELT extract: {error}") from error
        if lines != artifact["metadata"].get("records", lines):
            raise ValidationError("Record count differs from the download")
        return StageResult(records_in=lines, records_out=lines)

    # -- bronze / silver --------------------------------------------------------------------

    def ingest_bronze(self, artifact: dict, ctx: StageContext) -> StageResult:
        existing = current_bronze(BRONZE_DATASET, artifact["id"], self.parser_version)
        if existing and not ctx.params["rebuild_bronze"]:
            ctx.put(bronze_versions={BRONZE_DATASET: existing["id"]})
            self._advance_checkpoint(ctx, artifact)
            return StageResult.skipped(f"Bronze already exists for artifact #{artifact['id']}",
                                       records_out=existing["row_count"])
        from ..common import lineage_sql

        path = ctx.storage.local_path(artifact["storage_key"])
        columns = {"feed": "VARCHAR", "source_file": "VARCHAR", **{c: "VARCHAR" for c in GKG_COLUMNS}}
        select = ", ".join(f'"{c}"' for c in columns if c != "date")
        con = duck()
        try:
            with DatasetVersionWriter(ctx.storage, BRONZE_DATASET,
                                      {"country": artifact["country"], "artifact_id": artifact["id"]}, None,
                                      run_id=ctx.run_id, versions={"parser": self.parser_version},
                                      inputs=[{"type": "artifact", "id": artifact["id"],
                                               "sha256": artifact["sha256"]}]) as writer:
                rows = copy_query_into(writer, con, f"""
                    SELECT {select}, strptime("date", '%Y%m%d%H%M%S')::TIMESTAMPTZ AS published_at,
                           {lineage_sql(ctx, artifact, 'gdelt')}
                    FROM read_csv('{path}', delim = '\t', header = false, quote = '', escape = '',
                                  auto_detect = false, compression = 'gzip', columns = {columns})
                """)
        finally:
            con.close()
        ctx.put(bronze_versions={BRONZE_DATASET: writer.version["id"]})
        self._advance_checkpoint(ctx, artifact)
        return StageResult(records_in=rows, records_out=rows, bytes_processed=artifact["size_bytes"])

    @staticmethod
    def _advance_checkpoint(ctx: StageContext, artifact: dict) -> None:
        """Only once a window is safely in Bronze does the next run move past it."""
        last = artifact["metadata"].get("to")
        scope = f"gdelt:{artifact['country']}"
        current = repository.get_checkpoint(scope, "last_file")
        if last and (current is None or last > current):
            repository.set_checkpoint(scope, "last_file", last, ctx.run_id)

    def transform_silver(self, ctx: StageContext) -> StageResult:
        country = ctx.params["country"]
        bronze = repository.current_versions(BRONZE_DATASET, {"country": country})
        bronze = [v for v in bronze if v["versions"].get("parser") == self.parser_version]
        if not bronze:
            raise PermanentError(f"No GDELT Bronze for {country}")
        partition = {"source": "gdelt", "country": country}
        current = repository.current_versions("silver.org_mentions", partition)
        signature = {"transformation": self.transformation_version, "retention_days": ctx.params["retention_days"],
                     "as_of": datetime.now(timezone.utc).date().isoformat()}
        if current and not ctx.params["rebuild_silver"]:
            built_from = {i["id"] for i in current[0]["inputs"] if i.get("dataset_id") == BRONZE_DATASET}
            if built_from == {v["id"] for v in bronze} and current[0]["versions"] == signature:
                ctx.put(silver_versions={"silver.org_mentions": current[0]["id"]})
                return StageResult.skipped("Mentions are current for these GDELT windows")
        version, stats = silver.transform(ctx, country, bronze, signature, self.transformation_version,
                                         fips=fips_code(country))
        ctx.put(silver_versions={"silver.org_mentions": version["id"]})
        return StageResult(records_in=stats["records"], records_out=version["row_count"], metrics=stats)

    # -- quality & metrics --------------------------------------------------------------------

    def quality_checks(self, layer: str, dataset_id: str):
        if dataset_id == BRONZE_DATASET:
            return [RecordCountCheck(name="not_empty", min_rows=1, severity="warn"),
                    SchemaCheck(name="schema", required=(("gkg_record_id", "VARCHAR"), ("organizations", "VARCHAR"),
                                                         ("locations", "VARCHAR")))]
        return []

    def collect_metrics(self, ctx: StageContext) -> dict[str, float]:
        files = dataset_files("silver.org_mentions", {"source": "gdelt", "country": ctx.params["country"]})
        if not files:
            return {}
        con = duck()
        try:
            row = con.execute(f"SELECT count(*), sum(mentions), sum(articles) FROM {parquet_source(files)}").fetchone()
        finally:
            con.close()
        return dict(zip(["silver.org_mentions.organizations", "silver.org_mentions.mentions",
                         "silver.org_mentions.articles"], [float(v or 0) for v in row]))
