"""Wikidata source adapter: organizations, companies and brands as structured knowledge.

Selection: every item whose country (P17) is the requested country and whose class is a
subclass of organization (or business), plus every item that map sources reference as a
brand or wikidata tag. The artifact is a gzip JSON-lines snapshot of the full entity JSON,
so Bronze preserves exactly what Wikidata returned.

Downloads are incremental: one SPARQL query lists the selected items with their current
revision ids; entities whose revision is unchanged are copied from the previous snapshot
and only new or edited ones are fetched from the API. A snapshot identical to a stored
one (same items, same revisions) is not stored again.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from datetime import date

import pyarrow as pa
import pyarrow.parquet as pq

from ... import repository
from ...engine.context import StageContext
from ...errors import PermanentError, ValidationError
from ...lake.reader import dataset_files, duck, parquet_source, version_files
from ...lake.writer import DatasetVersionWriter
from ...normalize import NORMALIZER_VERSION
from ...observability import get_logger
from ...quality.checks import RecordCountCheck, SchemaCheck, UniqueCheck
from ..base import DataSourceAdapter, DiscoveredDataset, PipelineSpec, SourceCapabilities, StageResult
from ..common import current_bronze, silver_is_current, stage_file, store_artifact, verify_artifact
from . import entities as wd
from . import silver
from .client import BUSINESS, ORGANIZATION, WikidataClient

log = get_logger(__name__)

BRONZE_DATASET = "bronze.wikidata_entities"
FETCH_CHUNK = 500                   # entities fetched (and written) per chunk: bounds memory
BRONZE_SCHEMA = pa.schema([
    ("qid", pa.string()), ("role", pa.string()), ("revision", pa.int64()), ("classes", pa.list_(pa.string())),
    ("label_en", pa.string()), ("label_ur", pa.string()), ("description_en", pa.string()),
    ("modified", pa.timestamp("s", tz="UTC")), ("entity_json", pa.string()),
    ("source", pa.string()), ("artifact_id", pa.int64()), ("pipeline_run_id", pa.int64()),
    ("ingested_at", pa.timestamp("s", tz="UTC")),
])


def referenced_from_places(country: str) -> set[str]:
    """Brand / entity QIDs that map sources tag on places in this country."""
    files = dataset_files("silver.places", {"country": country})
    if not files:
        return set()
    con = duck()
    try:
        rows = con.execute(f"""
            SELECT DISTINCT q FROM (
                SELECT unnest(string_split(brand_wikidata, ';')) AS q FROM {parquet_source(files)}
                UNION ALL SELECT unnest(string_split(wikidata, ';')) FROM {parquet_source(files)}
            ) WHERE regexp_matches(trim(q), '^Q[0-9]+$')
        """).fetchall()
    finally:
        con.close()
    return {r[0].strip() for r in rows}


def line(qid: str, role: str, revision: int | None, classes: list[str], entity: dict) -> str:
    return json.dumps({"qid": qid, "role": role, "revision": revision, "classes": classes, "entity": entity},
                      ensure_ascii=False, separators=(",", ":")) + "\n"


class WikidataAdapter(DataSourceAdapter):
    role = "Organizations"
    source_id = "wikidata"
    name = "Wikidata"
    description = "Open knowledge base: companies, brands and institutions with websites, industries, " \
                  "headquarters, parents and identifiers."
    homepage = "https://www.wikidata.org"
    license = "CC0 1.0"
    capabilities = SourceCapabilities(supports_download=True, supports_incremental=True,
                                      provides_organizations=True)
    parser_version = "wikidata-bronze-1"
    transformation_version = f"wikidata-silver-1+{NORMALIZER_VERSION}"

    def pipelines(self) -> list[PipelineSpec]:
        return [PipelineSpec(
            id="wikidata.organizations",
            name="Wikidata organizations",
            description="Snapshot one country's organizations (and brands referenced by map data) from Wikidata.",
            parameters={
                "country": {"type": "string", "pattern": "^[A-Za-z]{2}$", "title": "Country",
                            "description": "ISO 3166-1 alpha-2 code, e.g. PK."},
                "scope": {"type": "string", "enum": ["organizations", "businesses"], "default": "organizations",
                          "title": "Scope", "description": "All organizations (incl. schools, universities, "
                                                           "agencies) or only businesses."},
                "include_referenced": {"type": "boolean", "default": True, "title": "Referenced brands",
                                       "description": "Also fetch brands / entities that OSM and Overture "
                                                      "places reference by QID (e.g. international chains)."},
                "max_entities": {"type": ["integer", "null"], "default": None, "minimum": 1,
                                 "title": "Max entities", "description": "Limit for trial runs. Empty = all."},
                "parallel_requests": {"type": "integer", "default": 2, "minimum": 1, "maximum": 4,
                                      "title": "Parallel API requests",
                                      "description": "Wikimedia asks clients to keep this low."},
            },
            required=["country"],
        )]

    def datasets(self) -> list[dict]:
        return [dict(dataset_id=BRONZE_DATASET, layer="bronze", name="Wikidata entities",
                     description="Full Wikidata entity JSON for the selected organizations and referenced items.",
                     source_id="wikidata", partition_columns=["country", "artifact_id"])]

    def check_parameters(self, params: dict) -> dict:
        params["country"] = params["country"].upper()
        return params

    def concurrency_key(self, params: dict) -> str:
        return f"wikidata:{params['country']}"

    def run_labels(self, params: dict):
        return params["country"], params.get("scope") or "organizations"

    # -- discover / download / validate -------------------------------------------------

    def discover_available_datasets(self, ctx: StageContext) -> list[DiscoveredDataset]:
        client = WikidataClient(should_cancel=ctx.check_cancelled)
        country = ctx.params["country"]
        ctx.progress(0, None, "queries", "resolving the country and organization classes", force=True)
        country_qid = client.country_qid(country)
        root = BUSINESS if ctx.params["scope"] == "businesses" else ORGANIZATION
        classes = client.organization_classes(country_qid, root)
        if not classes:
            raise PermanentError(f"Wikidata has no {ctx.params['scope']} for {country}")
        today = date.today().isoformat()
        return [DiscoveredDataset(
            dataset_name=ctx.params["scope"], url="https://query.wikidata.org/sparql", source_version=today,
            release_date=today, country=country, format="jsonl.gz",
            metadata={"country_qid": country_qid, "root_class": root, "classes": classes,
                      "estimated_items": sum(classes.values()), "queries": client.requests},
        )]

    def _previous(self, country: str, scope: str) -> dict | None:
        from ... import db

        with db.control() as conn:
            return conn.execute("""
                SELECT * FROM dp.dataset_artifacts WHERE source_id = 'wikidata' AND status = 'available'
                  AND country = %s AND dataset_name = %s ORDER BY id DESC LIMIT 1
            """, [country, scope]).fetchone()

    def download(self, ctx: StageContext) -> StageResult:
        discovered = ctx.get("discovered")
        params = ctx.params
        country, meta = params["country"], discovered["metadata"]
        client = WikidataClient(parallel=params["parallel_requests"], should_cancel=ctx.check_cancelled)

        ctx.progress(0, None, "queries", "listing the country's organizations", force=True)
        listed = {q: {"classes": classes, "role": "organization"}
                  for q, classes in client.items(meta["country_qid"], meta["classes"]).items()}
        if params["include_referenced"]:
            for q in referenced_from_places(country) - set(listed):
                listed[q] = {"classes": [], "role": "referenced"}
        revisions = client.revisions(listed, on_progress=lambda done, total: ctx.progress(
            done, total, "items", "reading current revision ids"))
        plan: dict[str, dict] = {q: {**v, "revision": revisions[q]} for q, v in listed.items() if q in revisions}
        if params["max_entities"]:
            plan = dict(sorted(plan.items())[:params["max_entities"]])
        if not plan:
            raise PermanentError("No Wikidata items matched")

        fingerprint = hashlib.sha256(json.dumps(sorted((q, v["revision"], v["role"]) for q, v in plan.items()))
                                     .encode()).hexdigest()[:24]
        checksum = f"wikidata:{country}:{params['scope']}:{fingerprint}"
        if not params["force_download"]:
            existing = repository.find_available_artifact(self.source_id, source_checksum=checksum)
            if existing:
                ctx.put(artifact_id=existing["id"])
                return StageResult.skipped(f"No Wikidata edits since artifact #{existing['id']} "
                                           f"({len(plan):,} items, same revisions)")

        filename = f"wikidata-{country}-{params['scope']}-{discovered['source_version']}.jsonl.gz"
        artifact = repository.create_artifact(
            source_id=self.source_id, dataset_name=params["scope"], country=country, source_url=discovered["url"],
            filename=filename, format="jsonl", compression="gzip", source_checksum=checksum,
            source_version=discovered["source_version"], source_release_date=discovered["release_date"],
            download_started_at=repository.now(), pipeline_run_id=ctx.run_id,
            metadata={k: v for k, v in meta.items() if k != "classes"} | {"class_count": len(meta["classes"])})
        local = stage_file(ctx, filename)
        previous = self._previous(country, params["scope"])
        reused = fetched = 0
        written: set[str] = set()
        mentioned: set[str] = set()
        labels_reused: dict[str, dict] = {}
        try:
            with gzip.open(local, "wt", encoding="utf-8", compresslevel=6) as out:
                # 1. unchanged entities straight from the previous snapshot (streamed, never all in memory)
                if previous and previous.get("storage_key") and ctx.storage.exists(previous["storage_key"]):
                    with gzip.open(ctx.storage.local_path(previous["storage_key"]), "rt", encoding="utf-8") as old:
                        for raw in old:
                            record = json.loads(raw)
                            q = record["qid"]
                            if record["role"] == "label":
                                labels_reused[q] = record["entity"]
                                continue
                            wanted = plan.get(q)
                            if wanted and wanted["revision"] == record["revision"] and q not in written:
                                out.write(line(q, wanted["role"], wanted["revision"], wanted["classes"],
                                               record["entity"]))
                                written.add(q)
                                mentioned |= wd.referenced_items(record["entity"])
                                reused += 1
                # 2. new and edited entities from the API, chunk by chunk
                missing = sorted(q for q in plan if q not in written)
                for start in range(0, len(missing), FETCH_CHUNK):
                    chunk = missing[start:start + FETCH_CHUNK]
                    entities = client.entities(chunk)
                    for q in chunk:
                        entity = entities.get(q)
                        if entity is None:
                            continue                          # deleted since the listing
                        wanted = plan[q]
                        out.write(line(q, wanted["role"], entity.get("lastrevid", wanted["revision"]),
                                       wanted["classes"], entity))
                        written.add(q)
                        mentioned |= wd.referenced_items(entity)
                        fetched += 1
                    ctx.progress(reused + start + len(chunk), len(plan), "entities",
                                 f"{fetched:,} fetched, {reused:,} unchanged")
                # 3. labels of referenced items (classes, industries, cities, parents...)
                label_ids = sorted(mentioned - written)
                to_fetch = [q for q in label_ids if q not in labels_reused]
                fresh = client.entities(to_fetch, labels_only=True, on_progress=lambda d, t: ctx.progress(
                    d, t, "batches", "fetching labels of referenced items")) if to_fetch else {}
                for q in label_ids:
                    entity = fresh.get(q) or labels_reused.get(q)
                    if entity is not None:
                        out.write(line(q, "label", None, [], {"id": q, "labels": entity.get("labels", {})}))
        except Exception:
            repository.update_artifact(artifact["id"], status="failed")
            raise
        stored = store_artifact(ctx, artifact, local, items=len(written), fetched=fetched, reused=reused,
                                labels=len(label_ids), api_requests=client.requests)
        return StageResult(records_in=len(plan), records_out=len(written), bytes_processed=stored["size_bytes"],
                           metrics={"entities": len(written), "fetched": fetched, "reused_unchanged": reused,
                                    "labels": len(label_ids), "api_requests": client.requests})

    def validate_download(self, artifact: dict, ctx: StageContext) -> StageResult:
        path = verify_artifact(ctx, artifact)
        roles: dict[str, int] = {}
        try:
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                for raw in handle:
                    record = json.loads(raw)
                    if not record.get("qid") or "entity" not in record:
                        raise ValidationError("Snapshot line without qid/entity")
                    roles[record["role"]] = roles.get(record["role"], 0) + 1
        except (OSError, json.JSONDecodeError) as error:
            raise ValidationError(f"Unreadable Wikidata snapshot: {error}") from error
        items = roles.get("organization", 0) + roles.get("referenced", 0)
        if items != artifact["metadata"].get("items", items):
            raise ValidationError(f"Snapshot has {items} items, expected {artifact['metadata']['items']}")
        return StageResult(records_in=sum(roles.values()), records_out=items, metrics=roles)

    # -- bronze / silver --------------------------------------------------------------------

    def ingest_bronze(self, artifact: dict, ctx: StageContext) -> StageResult:
        existing = current_bronze(BRONZE_DATASET, artifact["id"], self.parser_version)
        if existing and not ctx.params["rebuild_bronze"]:
            ctx.put(bronze_versions={BRONZE_DATASET: existing["id"]})
            return StageResult.skipped(f"Bronze already exists for artifact #{artifact['id']}",
                                       records_out=existing["row_count"])
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).replace(microsecond=0)
        partition = {"country": artifact["country"], "artifact_id": artifact["id"]}
        columns = {f.name: [] for f in BRONZE_SCHEMA}
        done = 0
        with DatasetVersionWriter(ctx.storage, BRONZE_DATASET, partition, BRONZE_SCHEMA, run_id=ctx.run_id,
                                  versions={"parser": self.parser_version},
                                  inputs=[{"type": "artifact", "id": artifact["id"], "sha256": artifact["sha256"]}]) \
                as writer, gzip.open(ctx.storage.local_path(artifact["storage_key"]), "rt", encoding="utf-8") as handle:
            for raw in handle:
                record = json.loads(raw)
                entity = record["entity"]
                for key, value in (("qid", record["qid"]), ("role", record["role"]), ("revision", record["revision"]),
                                   ("classes", record["classes"]), ("label_en", wd.label(entity, "en")),
                                   ("label_ur", wd.label(entity, "ur")),
                                   ("description_en", entity.get("descriptions", {}).get("en", {}).get("value")),
                                   ("modified", wd.modified(entity)),
                                   ("entity_json", json.dumps(entity, ensure_ascii=False, separators=(",", ":"))),
                                   ("source", "wikidata"), ("artifact_id", artifact["id"]),
                                   ("pipeline_run_id", ctx.run_id), ("ingested_at", now)):
                    columns[key].append(value)
                done += 1
                if done % 5000 == 0:
                    writer.write_batch(pa.table(columns, schema=BRONZE_SCHEMA))
                    columns = {f.name: [] for f in BRONZE_SCHEMA}
                    ctx.progress(done, None, "entities", "parsing the snapshot")
            writer.write_batch(pa.table(columns, schema=BRONZE_SCHEMA))
        ctx.put(bronze_versions={BRONZE_DATASET: writer.version["id"]})
        return StageResult(records_in=done, records_out=writer.row_count, bytes_processed=artifact["size_bytes"])

    def transform_silver(self, ctx: StageContext) -> StageResult:
        artifact_id = ctx.get("artifact_id") or ctx.params.get("artifact_id")
        artifact = repository.get_artifact(artifact_id) if artifact_id else self.latest_artifact(ctx.params)
        if artifact is None:
            raise PermanentError("No Wikidata artifact available to build Silver from")
        bronze = current_bronze(BRONZE_DATASET, artifact["id"], self.parser_version)
        if bronze is None:
            raise PermanentError(f"Bronze is missing for artifact #{artifact['id']}; start from BRONZE_INGEST")
        partition = {"source": "wikidata", "country": artifact["country"]}
        current = silver_is_current(ctx, ["silver.organizations"], partition, {bronze["id"]},
                                    self.transformation_version)
        if current:
            ctx.put(silver_versions=current)
            return StageResult.skipped("Silver is current for this Bronze and transformation version")
        version, stats = silver.transform(ctx, artifact, bronze, version_files(bronze), self.transformation_version)
        ctx.put(silver_versions={"silver.organizations": version["id"]})
        return StageResult(records_in=stats["items"], records_out=version["row_count"], metrics=stats)

    # -- quality & metrics --------------------------------------------------------------------

    def quality_checks(self, layer: str, dataset_id: str):
        if dataset_id == BRONZE_DATASET:
            return [UniqueCheck(name="unique_qid", columns=("qid",)),
                    RecordCountCheck(name="not_empty", min_rows=1),
                    SchemaCheck(name="schema", required=(("qid", "VARCHAR"), ("entity_json", "VARCHAR"),
                                                         ("revision", "BIGINT")))]
        return []

    def collect_metrics(self, ctx: StageContext) -> dict[str, float]:
        files = dataset_files("silver.organizations", {"source": "wikidata", "country": ctx.params["country"]})
        if not files:
            return {}
        con = duck()
        try:
            row = con.execute(f"""
                SELECT count(*), count(normalized_domain), count(latitude), count(industries[1]),
                       count(parent_id), count(*) FILTER (WHERE identifiers <> '{{}}')
                FROM {parquet_source(files)}
            """).fetchone()
        finally:
            con.close()
        return dict(zip(["silver.organizations.records", "silver.organizations.with_domain",
                         "silver.organizations.with_coordinates", "silver.organizations.with_industry",
                         "silver.organizations.with_parent", "silver.organizations.with_identifiers"],
                        [float(v or 0) for v in row]))


def read_bronze(files: list[str], columns: list[str]):
    for path in files:
        yield from pq.ParquetFile(path).iter_batches(batch_size=2000, columns=columns)
