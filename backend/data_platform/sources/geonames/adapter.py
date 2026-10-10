"""GeoNames source adapter: the geographic reference dimension (gazetteer, admin names, postal codes).

The official daily country dumps are downloaded as-is (country file, alternate names,
postal codes, admin code tables, country info) and kept together in one bundle artifact
with a manifest of per-file checksums. GeoNames' populated places become the preferred
gazetteer for city / locality assignment of every source; spot features that are
businesses or institutions (hotels, hospitals, mills, factories...) join silver.places.
"""

from __future__ import annotations

import hashlib
import json
import os
import zipfile
from pathlib import Path

from ... import http, repository
from ...engine.context import StageContext
from ...errors import PermanentError, ValidationError
from ...lake.reader import dataset_files, duck, parquet_source
from ...lake.writer import DatasetVersionWriter, copy_query_into
from ...normalize import NORMALIZER_VERSION
from ...quality.checks import RecordCountCheck, SchemaCheck, UniqueCheck
from ..base import DataSourceAdapter, DiscoveredDataset, PipelineSpec, SourceCapabilities, StageResult
from ..common import (
    current_bronze,
    discard_staging,
    lineage_sql,
    reuse_artifact,
    stage_file,
    store_artifact,
    verify_artifact,
)
from . import silver

PLACES = "bronze.geonames_places"
ALTERNATE_NAMES = "bronze.geonames_alternate_names"
ADMIN_CODES = "bronze.geonames_admin_codes"
POSTAL_CODES = "bronze.geonames_postal_codes"
COUNTRIES = "bronze.geonames_countries"
BRONZE_DATASETS = (PLACES, ALTERNATE_NAMES, ADMIN_CODES, POSTAL_CODES, COUNTRIES)

PLACE_COLUMNS = {
    "geonameid": "BIGINT", "name": "VARCHAR", "asciiname": "VARCHAR", "alternatenames": "VARCHAR",
    "latitude": "DOUBLE", "longitude": "DOUBLE", "feature_class": "VARCHAR", "feature_code": "VARCHAR",
    "country_code": "VARCHAR", "cc2": "VARCHAR", "admin1_code": "VARCHAR", "admin2_code": "VARCHAR",
    "admin3_code": "VARCHAR", "admin4_code": "VARCHAR", "population": "BIGINT", "elevation": "INTEGER",
    "dem": "INTEGER", "timezone": "VARCHAR", "modification_date": "DATE",
}
ALTERNATE_NAME_COLUMNS = {
    "alternate_name_id": "BIGINT", "geonameid": "BIGINT", "isolanguage": "VARCHAR", "alternate_name": "VARCHAR",
    "is_preferred_name": "VARCHAR", "is_short_name": "VARCHAR", "is_colloquial": "VARCHAR",
    "is_historic": "VARCHAR", "from_period": "VARCHAR", "to_period": "VARCHAR",
}
POSTAL_COLUMNS = {
    "country_code": "VARCHAR", "postal_code": "VARCHAR", "place_name": "VARCHAR", "admin1_name": "VARCHAR",
    "admin1_code": "VARCHAR", "admin2_name": "VARCHAR", "admin2_code": "VARCHAR", "admin3_name": "VARCHAR",
    "admin3_code": "VARCHAR", "latitude": "DOUBLE", "longitude": "DOUBLE", "accuracy": "SMALLINT",
}
ADMIN_COLUMNS = {"code": "VARCHAR", "name": "VARCHAR", "asciiname": "VARCHAR", "geonameid": "BIGINT"}
# countryInfo.txt starts with '#' comment lines (one is a tab-separated header), and data rows contain
# '#' too (postal formats like '#####'): read everything as text and drop rows whose first field is a comment.
COUNTRY_COLUMNS = {name: "VARCHAR" for name in (
    "iso", "iso3", "iso_numeric", "fips", "country", "capital", "area_km2", "population", "continent", "tld",
    "currency_code", "currency_name", "phone", "postal_code_format", "postal_code_regex", "languages", "geonameid",
    "neighbours", "equivalent_fips_code")}


def base_url() -> str:
    return os.environ.get("GEONAMES_BASE_URL", "https://download.geonames.org/export").rstrip("/")


def remote_files(country: str, params: dict) -> list[dict]:
    """Files making up one country's GeoNames snapshot: (path inside the bundle, url, required)."""
    files = [("dump/{c}.zip", True), ("dump/admin1CodesASCII.txt", True), ("dump/admin2Codes.txt", True),
             ("dump/countryInfo.txt", True)]
    if params.get("include_alternate_names", True):
        files.append(("dump/alternatenames/{c}.zip", False))
    if params.get("include_postal_codes", True):
        files.append(("zip/{c}.zip", False))
    return [{"path": path.format(c=country), "url": f"{base_url()}/{path.format(c=country)}", "required": required}
            for path, required in files]


def csv_source(path: Path, columns: dict[str, str], ragged: bool = False) -> str:
    """GeoNames files are tab-separated with no quoting (names may contain quote characters)."""
    options = "delim = '\t', header = false, quote = '', escape = '', auto_detect = false"
    if ragged:
        options += ", null_padding = true, strict_mode = false"
    return f"read_csv('{path}', {options}, columns = {json.dumps(columns)})"


class GeoNamesAdapter(DataSourceAdapter):
    role = "Gazetteer · facilities"
    source_id = "geonames"
    name = "GeoNames"
    description = "Gazetteer of every city, town and village with population, admin names and postal codes."
    homepage = "https://www.geonames.org"
    license = "CC BY 4.0"
    capabilities = SourceCapabilities(supports_download=True, supports_release_detection=True,
                                      provides_places=True, provides_geography=True)
    parser_version = "geonames-bronze-1"
    transformation_version = f"geonames-silver-1+{NORMALIZER_VERSION}"

    def pipelines(self) -> list[PipelineSpec]:
        return [PipelineSpec(
            id="geonames.country",
            name="GeoNames country gazetteer",
            description="Download one country's GeoNames dump: populated places, admin divisions, alternate names, "
                        "postal codes and notable facilities.",
            parameters={
                "country": {"type": "string", "pattern": "^[A-Za-z]{2}$", "title": "Country",
                            "description": "ISO 3166-1 alpha-2 code, e.g. PK."},
                "include_alternate_names": {"type": "boolean", "default": True, "title": "Alternate names",
                                            "description": "Local-language names and Wikidata links."},
                "include_postal_codes": {"type": "boolean", "default": True, "title": "Postal codes"},
            },
            required=["country"],
        )]

    def datasets(self) -> list[dict]:
        common = dict(layer="bronze", source_id="geonames", partition_columns=["country", "artifact_id"])
        return [
            dict(dataset_id=PLACES, name="GeoNames features", description="Every GeoNames feature in the country.",
                 **common),
            dict(dataset_id=ALTERNATE_NAMES, name="GeoNames alternate names",
                 description="Names in other languages, links and Wikidata ids.", **common),
            dict(dataset_id=ADMIN_CODES, name="GeoNames admin codes", description="Admin level 1/2 code tables.",
                 **common),
            dict(dataset_id=POSTAL_CODES, name="GeoNames postal codes", description="Postal codes.", **common),
            dict(dataset_id=COUNTRIES, name="GeoNames countries",
                 description="Country info: ISO/FIPS codes, TLD, phone prefix, languages.", **common),
        ]

    def check_parameters(self, params: dict) -> dict:
        params["country"] = params["country"].upper()
        return params

    def concurrency_key(self, params: dict) -> str:
        return f"geonames:{params['country']}"

    def run_labels(self, params: dict):
        return params["country"], "gazetteer"

    # -- discover / download / validate -------------------------------------------------

    def _discover(self, params: dict) -> DiscoveredDataset:
        country = params["country"]
        files = []
        for spec in remote_files(country, params):
            try:
                info = http.head(spec["url"])
            except PermanentError:
                if spec["required"]:
                    raise PermanentError(f"GeoNames has no {spec['path']} (is {country} a valid country code?)")
                continue                                   # e.g. no postal codes published for this country
            files.append({**spec, "size_bytes": info.size_bytes, "etag": info.etag,
                          "last_modified": info.last_modified})
        fingerprint = hashlib.sha256(json.dumps(
            [(f["path"], f["etag"], f["size_bytes"], f["last_modified"]) for f in files]).encode()).hexdigest()[:16]
        modified = max((f["last_modified"] or "") for f in files)[:10] or None
        return DiscoveredDataset(
            dataset_name="gazetteer", url=f"{base_url()}/dump/{country}.zip", source_version=modified,
            release_date=modified, size_bytes=sum(f["size_bytes"] or 0 for f in files),
            checksum=f"geonames:{country}:{fingerprint}", country=country, format="zip",
            metadata={"files": files},
        )

    def latest_release(self, params: dict) -> DiscoveredDataset | None:
        return self._discover(params)

    def discover_available_datasets(self, ctx: StageContext) -> list[DiscoveredDataset]:
        return [self._discover(ctx.params)]

    def download(self, ctx: StageContext) -> StageResult:
        discovered = ctx.get("discovered")
        country = ctx.params["country"]
        existing = reuse_artifact(ctx, self.source_id, discovered["checksum"])
        if existing:
            return StageResult.skipped(f"GeoNames snapshot for {country} already stored (artifact #{existing['id']})")
        files = discovered["metadata"]["files"]
        total = sum(f["size_bytes"] or 0 for f in files) or None
        ctx.storage.ensure_free_space(2 * (total or 0), ctx.settings.min_free_disk_bytes)
        filename = f"geonames-{country}-{discovered['source_version']}.zip"
        artifact = repository.create_artifact(
            source_id=self.source_id, dataset_name="gazetteer", country=country, source_url=discovered["url"],
            filename=filename, format="zip", compression="zip", source_checksum=discovered["checksum"],
            source_version=discovered["source_version"], source_release_date=discovered["release_date"],
            download_started_at=repository.now(), pipeline_run_id=ctx.run_id, metadata=discovered["metadata"])
        bundle = stage_file(ctx, filename)
        manifest = []
        done = 0
        try:
            with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_STORED) as archive:
                for spec in files:
                    part = bundle.parent / (spec["path"].replace("/", "__") + ".part")
                    result = http.download_to(spec["url"], part, on_progress=lambda written, _t: ctx.progress(
                        done + written, total, "bytes", f"downloading {spec['path']}"))
                    archive.write(part, spec["path"])
                    manifest.append({"path": spec["path"], "url": spec["url"], "size_bytes": result.size_bytes,
                                     "sha256": result.sha256, "md5": result.md5, "etag": spec["etag"],
                                     "last_modified": spec["last_modified"]})
                    done += result.size_bytes
                    part.unlink()
                archive.writestr("manifest.json", json.dumps({"source": "geonames", "country": country,
                                                              "files": manifest}, indent=2))
        except Exception:
            repository.update_artifact(artifact["id"], status="failed")
            raise
        stored = store_artifact(ctx, artifact, bundle, files=manifest)
        return StageResult(records_out=len(manifest), bytes_processed=stored["size_bytes"],
                           metrics={"files": len(manifest), "downloaded_bytes": done})

    def validate_download(self, artifact: dict, ctx: StageContext) -> StageResult:
        path = verify_artifact(ctx, artifact)
        country = artifact["country"]
        with zipfile.ZipFile(path) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            for entry in manifest["files"]:
                digest = hashlib.sha256()
                with archive.open(entry["path"]) as handle:
                    while chunk := handle.read(1 << 20):
                        digest.update(chunk)
                if digest.hexdigest() != entry["sha256"]:
                    raise ValidationError(f"{entry['path']} does not match its manifest checksum")
            with zipfile.ZipFile(archive.open(f"dump/{country}.zip")) as inner:
                with inner.open(f"{country}.txt") as handle:
                    first = handle.readline().decode("utf-8").rstrip("\n").split("\t")
        if len(first) != len(PLACE_COLUMNS):
            raise ValidationError(f"GeoNames format changed: expected {len(PLACE_COLUMNS)} columns, got {len(first)}")
        return StageResult(records_in=len(manifest["files"]), records_out=len(manifest["files"]),
                           bytes_processed=artifact["size_bytes"], metrics={"files": len(manifest["files"])})

    # -- bronze ---------------------------------------------------------------------------

    def _current_bronze(self, artifact_id: int) -> dict[str, dict] | None:
        versions = {d: current_bronze(d, artifact_id, self.parser_version) for d in BRONZE_DATASETS}
        return versions if all(versions.values()) else None

    def ingest_bronze(self, artifact: dict, ctx: StageContext) -> StageResult:
        existing = self._current_bronze(artifact["id"])
        if existing and not ctx.params["rebuild_bronze"]:
            ctx.put(bronze_versions={d: v["id"] for d, v in existing.items()})
            return StageResult.skipped(f"Bronze already exists for artifact #{artifact['id']}",
                                       records_out=existing[PLACES]["row_count"])
        country = artifact["country"]
        work = stage_file(ctx, "extracted")
        work.mkdir()
        with zipfile.ZipFile(ctx.storage.local_path(artifact["storage_key"])) as archive:
            members = set(archive.namelist())

            def extract(member: str, target: str, inner: str | None = None) -> Path | None:
                """Copy one bundled file (or a file inside a bundled zip) to `target` in the work directory."""
                if member not in members:
                    return None
                path = work / target
                with (zipfile.ZipFile(archive.open(member)).open(inner) if inner else archive.open(member)) as src, \
                        open(path, "wb") as dst:
                    while chunk := src.read(1 << 20):
                        dst.write(chunk)
                return path

            places = extract(f"dump/{country}.zip", "places.txt", f"{country}.txt")
            alternate = extract(f"dump/alternatenames/{country}.zip", "alternate.txt", f"{country}.txt")
            postal = extract(f"zip/{country}.zip", "postal.txt", f"{country}.txt")
            admin1 = extract("dump/admin1CodesASCII.txt", "admin1.txt")
            admin2 = extract("dump/admin2Codes.txt", "admin2.txt")
            countries = extract("dump/countryInfo.txt", "countries.txt")

        lineage = lineage_sql(ctx, artifact, "geonames")
        partition = {"country": country, "artifact_id": artifact["id"]}
        common = dict(run_id=ctx.run_id, versions={"parser": self.parser_version},
                      inputs=[{"type": "artifact", "id": artifact["id"], "sha256": artifact["sha256"]}])
        empty = {
            ALTERNATE_NAMES: ALTERNATE_NAME_COLUMNS, POSTAL_CODES: POSTAL_COLUMNS,
        }
        queries = {
            PLACES: f"SELECT *, {lineage} FROM {csv_source(places, PLACE_COLUMNS)}",
            ALTERNATE_NAMES: f"SELECT *, {lineage} FROM {csv_source(alternate, ALTERNATE_NAME_COLUMNS)}"
            if alternate else None,
            ADMIN_CODES: f"""
                SELECT 1::SMALLINT AS admin_level, *, {lineage} FROM {csv_source(admin1, ADMIN_COLUMNS)}
                WHERE code LIKE '{country}.%'
                UNION ALL
                SELECT 2::SMALLINT, *, {lineage} FROM {csv_source(admin2, ADMIN_COLUMNS)}
                WHERE code LIKE '{country}.%'
            """,
            POSTAL_CODES: f"SELECT *, {lineage} FROM {csv_source(postal, POSTAL_COLUMNS)}" if postal else None,
            COUNTRIES: f"SELECT *, {lineage} FROM {csv_source(countries, COUNTRY_COLUMNS, ragged=True)} "
                       "WHERE iso NOT LIKE '#%' AND iso IS NOT NULL",
        }
        written, rows = {}, {}
        con = duck()
        try:
            for index, (dataset_id, sql) in enumerate(queries.items()):
                ctx.progress(index, len(queries), "datasets", f"loading {dataset_id}")
                if sql is None:                   # optional file not published: an empty, typed version
                    columns = ", ".join(f"NULL::{t} AS {c}" for c, t in empty[dataset_id].items())
                    sql = f"SELECT {columns}, {lineage} WHERE false"
                with DatasetVersionWriter(ctx.storage, dataset_id, partition, None, **common) as writer:
                    rows[dataset_id] = copy_query_into(writer, con, sql)
                written[dataset_id] = writer.version
        finally:
            con.close()
            discard_staging(ctx)
        ctx.put(bronze_versions={d: v["id"] for d, v in written.items()})
        return StageResult(records_in=sum(rows.values()), records_out=rows[PLACES],
                           bytes_processed=artifact["size_bytes"], metrics={f"rows.{d}": n for d, n in rows.items()})

    # -- silver ---------------------------------------------------------------------------

    def transform_silver(self, ctx: StageContext) -> StageResult:
        artifact_id = ctx.get("artifact_id") or ctx.params.get("artifact_id")
        artifact = repository.get_artifact(artifact_id) if artifact_id else self.latest_artifact(ctx.params)
        if artifact is None:
            raise PermanentError("No GeoNames artifact available to build Silver from")
        bronze = self._current_bronze(artifact["id"])
        if bronze is None:
            raise PermanentError(f"Bronze is missing for artifact #{artifact['id']}; start from BRONZE_INGEST")
        written, stats = silver.transform(ctx, artifact, bronze, self.transformation_version)
        if written is None:
            ctx.put(silver_versions=stats.pop("current"))
            return StageResult.skipped("Silver is current for this Bronze, geography and transformation version",
                                       records_out=stats.get("places"))
        ctx.put(silver_versions={d: v["id"] for d, v in written.items()})
        return StageResult(records_in=stats["features"], records_out=stats["places"], metrics=stats)

    # -- quality & metrics ----------------------------------------------------------------

    def quality_checks(self, layer: str, dataset_id: str):
        if dataset_id == PLACES:
            return [UniqueCheck(name="unique_geonameid", columns=("geonameid",)),
                    RecordCountCheck(name="not_empty", min_rows=1),
                    SchemaCheck(name="schema", required=(("geonameid", "BIGINT"), ("latitude", "DOUBLE"),
                                                         ("longitude", "DOUBLE"), ("feature_code", "VARCHAR")))]
        if dataset_id == ADMIN_CODES:
            return [UniqueCheck(name="unique_code", columns=("code",)),
                    RecordCountCheck(name="not_empty", min_rows=1)]
        if dataset_id == COUNTRIES:
            return [UniqueCheck(name="unique_iso", columns=("iso",))]
        return []

    def collect_metrics(self, ctx: StageContext) -> dict[str, float]:
        partition = {"source": "geonames", "country": ctx.params["country"]}
        files = dataset_files("silver.geo_places", partition)
        if not files:
            return {}
        con = duck()
        try:
            row = con.execute(f"""
                SELECT count(*), count(*) FILTER (WHERE kind = 'city'), count(*) FILTER (WHERE kind = 'town'),
                       count(*) FILTER (WHERE population > 0), count(name_local)
                FROM {parquet_source(files)}
            """).fetchone()
        finally:
            con.close()
        return dict(zip(["silver.geo_places.places", "silver.geo_places.cities", "silver.geo_places.towns",
                         "silver.geo_places.with_population", "silver.geo_places.with_local_name"],
                        [float(v or 0) for v in row]))
