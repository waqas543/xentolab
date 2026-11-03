"""Company websites source adapter: what the scraping engine read on companies' own websites.

The scraping engine (data_platform.scrape) crawls continuously and keeps its parsed output in web.pages /
web.facts (raw HTML is deleted once parsed). This adapter is the batch side of that source, like any other
provider: each run snapshots the country's collected facts as the raw artifact, keeps them in Bronze, and maps
them onto the shared silver.domain_facts contract. The platform's Gold build then merges them with every other
source into gold.domain_profiles and publishes it. A finished crawl campaign queues a run automatically.
"""

from __future__ import annotations

import gzip
import json

from ... import db, repository
from ...engine.context import StageContext
from ...errors import PermanentError, ValidationError
from ...lake.reader import dataset_files, duck, parquet_source
from ...lake.writer import DatasetVersionWriter, copy_query_into
from ...normalize import NORMALIZER_VERSION
from ...quality.checks import RecordCountCheck, SchemaCheck
from ..base import DataSourceAdapter, DiscoveredDataset, PipelineSpec, SourceCapabilities, StageResult
from ..common import (current_bronze, lineage_sql, reuse_artifact, silver_is_current, stage_file, store_artifact,
                      verify_artifact)
from . import silver

BRONZE_DATASET = "bronze.website_facts"
PIPELINE_ID = "websites.profiles"
# One line per fact in the artifact, in this order.
COLUMNS = {
    "domain": "VARCHAR", "field": "VARCHAR", "value": "VARCHAR", "method": "VARCHAR", "evidence": "VARCHAR",
    "evidence_url": "VARCHAR", "page_kind": "VARCHAR", "page_title": "VARCHAR", "http_status": "INTEGER",
    "confidence": "DOUBLE", "extractor_version": "VARCHAR", "campaign_id": "BIGINT", "extracted_at": "TIMESTAMPTZ",
    "fetched_at": "TIMESTAMPTZ",
}
_IN_COUNTRY = "f.domain IN (SELECT domain FROM web.crawl_domains WHERE country_code = %(country)s)"


def collected(country: str) -> dict:
    """How much the scraping engine has collected for a country (the 'release' this source offers)."""
    with db.serving() as conn:
        return conn.execute(f"""
            SELECT count(*) AS facts, count(DISTINCT f.domain) AS domains, max(f.extracted_at) AS latest
            FROM web.facts f WHERE {_IN_COUNTRY}
        """, {"country": country}).fetchone()


class WebsitesAdapter(DataSourceAdapter):
    role = "Company profiles"
    source_id = "websites"
    name = "Company websites"
    description = ("Facts read from companies' own websites by the scraping engine: summary, industry, city, "
                   "founded year, social profiles and contacts.")
    homepage = None
    license = "Facts from public web pages; page copies are not kept or published"
    capabilities = SourceCapabilities(supports_download=True, supports_release_detection=True)
    parser_version = "websites-bronze-1"
    transformation_version = f"websites-silver-1+{NORMALIZER_VERSION}"

    def pipelines(self) -> list[PipelineSpec]:
        return [PipelineSpec(
            id=PIPELINE_ID,
            name="Website profiles",
            description="Ingest the facts the scraping engine collected for one country into Bronze and "
                        "silver.domain_facts, then rebuild Gold domain profiles.",
            parameters={"country": {"type": "string", "pattern": "^[A-Za-z]{2}$", "title": "Country",
                                    "description": "ISO 3166-1 alpha-2 code, e.g. PK."}},
            required=["country"],
        )]

    def datasets(self) -> list[dict]:
        return [dict(dataset_id=BRONZE_DATASET, layer="bronze", name="Website facts (raw)",
                     description="Facts the scraping engine extracted from company websites, as collected.",
                     source_id=self.source_id, partition_columns=["country", "artifact_id"])]

    def check_parameters(self, params: dict) -> dict:
        params["country"] = params["country"].upper()
        return params

    def concurrency_key(self, params: dict) -> str:
        return f"websites:{params['country']}"

    def run_labels(self, params: dict):
        return params["country"], "website facts"

    def _discovered(self, country: str) -> DiscoveredDataset:
        state = collected(country)
        latest = state["latest"].isoformat() if state["latest"] else None
        return DiscoveredDataset(
            dataset_name="facts", url=None, source_version=latest, country=country, format="jsonl",
            release_date=state["latest"].date().isoformat() if state["latest"] else None,
            checksum=f"websites:{country}:{state['facts']}:{latest}",
            metadata={"facts": state["facts"], "domains": state["domains"], "latest": latest})

    def latest_release(self, params: dict) -> DiscoveredDataset | None:
        return self._discovered(params["country"].upper())

    # -- discover / download / validate -------------------------------------------------

    def discover_available_datasets(self, ctx: StageContext) -> list[DiscoveredDataset]:
        discovered = self._discovered(ctx.params["country"])
        if not discovered.metadata["facts"]:
            raise PermanentError(f"The scraping engine has collected nothing for {ctx.params['country']} yet: "
                                 "seed a campaign first (make scrape-seed)")
        return [discovered]

    def download(self, ctx: StageContext) -> StageResult:
        discovered = ctx.get("discovered")
        country, meta = ctx.params["country"], discovered["metadata"]
        existing = reuse_artifact(ctx, self.source_id, discovered["checksum"])
        if existing:
            return StageResult.skipped(f"Nothing new since artifact #{existing['id']}")
        filename = f"website-facts-{country}.jsonl.gz"
        artifact = repository.create_artifact(
            source_id=self.source_id, dataset_name="facts", country=country, source_url=None, filename=filename,
            format="jsonl", compression="gzip", source_checksum=discovered["checksum"],
            source_version=discovered["source_version"], source_release_date=discovered["release_date"],
            download_started_at=repository.now(), pipeline_run_id=ctx.run_id, metadata=meta)
        local = stage_file(ctx, filename)
        rows = 0
        try:
            with gzip.open(local, "wt", encoding="utf-8") as out, db.serving() as conn, conn.transaction(), \
                    conn.cursor(name="website_facts_export") as cur:
                cur.itersize = 20_000
                cur.execute(f"""
                    SELECT f.domain, f.field, f.value, f.source AS method, f.evidence, f.url AS evidence_url,
                           p.kind AS page_kind, p.title AS page_title, p.http_status, f.confidence,
                           f.extractor_version, f.campaign_id, f.extracted_at, p.fetched_at
                    FROM web.facts f LEFT JOIN web.pages p ON p.domain = f.domain AND p.url = f.url
                    WHERE {_IN_COUNTRY} ORDER BY f.domain, f.id
                """, {"country": country})
                for row in cur:
                    out.write(json.dumps(row, default=str, ensure_ascii=False) + "\n")
                    rows += 1
                    if rows % 20_000 == 0:
                        ctx.progress(rows, meta["facts"], "facts", "exporting collected facts")
        except Exception:
            repository.update_artifact(artifact["id"], status="failed")
            raise
        stored = store_artifact(ctx, artifact, local, records=rows)
        return StageResult(records_out=rows, bytes_processed=stored["size_bytes"],
                           metrics={"facts": rows, "domains": meta["domains"]})

    def validate_download(self, artifact: dict, ctx: StageContext) -> StageResult:
        path = verify_artifact(ctx, artifact)
        lines = 0
        try:
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                for raw in handle:
                    record = json.loads(raw)
                    if not record.get("domain") or not record.get("field"):
                        raise ValidationError(f"Line {lines + 1} has no domain or field")
                    lines += 1
        except (OSError, EOFError, ValueError) as error:
            raise ValidationError(f"Corrupt website-facts artifact: {error}") from error
        if lines != artifact["metadata"].get("records", lines):
            raise ValidationError("Fact count differs from the export")
        return StageResult(records_in=lines, records_out=lines)

    # -- bronze / silver --------------------------------------------------------------------

    def ingest_bronze(self, artifact: dict, ctx: StageContext) -> StageResult:
        existing = current_bronze(BRONZE_DATASET, artifact["id"], self.parser_version)
        if existing and not ctx.params["rebuild_bronze"]:
            ctx.put(bronze_versions={BRONZE_DATASET: existing["id"]})
            return StageResult.skipped(f"Bronze already exists for artifact #{artifact['id']}",
                                       records_out=existing["row_count"])
        path = ctx.storage.local_path(artifact["storage_key"])
        con = duck()
        try:
            with DatasetVersionWriter(ctx.storage, BRONZE_DATASET,
                                      {"country": artifact["country"], "artifact_id": artifact["id"]}, None,
                                      run_id=ctx.run_id, versions={"parser": self.parser_version},
                                      inputs=[{"type": "artifact", "id": artifact["id"],
                                               "sha256": artifact["sha256"]}]) as writer:
                rows = copy_query_into(writer, con, f"""
                    SELECT *, {lineage_sql(ctx, artifact, 'website')}
                    FROM read_json('{path}', format = 'newline_delimited', columns = {COLUMNS})
                """)
        finally:
            con.close()
        ctx.put(bronze_versions={BRONZE_DATASET: writer.version["id"]})
        return StageResult(records_in=rows, records_out=rows, bytes_processed=artifact["size_bytes"])

    def transform_silver(self, ctx: StageContext) -> StageResult:
        artifact_id = ctx.get("artifact_id") or ctx.params.get("artifact_id")
        artifact = repository.get_artifact(artifact_id) if artifact_id else self.latest_artifact(ctx.params)
        if artifact is None:
            raise PermanentError("No website-facts artifact to build Silver from")
        bronze = current_bronze(BRONZE_DATASET, artifact["id"], self.parser_version)
        if bronze is None:
            raise PermanentError(f"Bronze is missing for artifact #{artifact['id']}; start from BRONZE_INGEST")
        partition = {"source": "website", "country": artifact["country"]}
        current = silver_is_current(ctx, ["silver.domain_facts"], partition, {bronze["id"]},
                                    self.transformation_version)
        if current:
            ctx.put(silver_versions=current)
            return StageResult.skipped("Silver is current for this Bronze and transformation version")
        version, stats = silver.transform(ctx, artifact, bronze, self.transformation_version)
        ctx.put(silver_versions={"silver.domain_facts": version["id"]})
        return StageResult(records_in=stats["facts"], records_out=version["row_count"],
                           records_rejected=stats["unknown_field"] + stats["unknown_method"] + stats["empty"],
                           metrics=stats)

    # -- quality & metrics --------------------------------------------------------------------

    def quality_checks(self, layer: str, dataset_id: str):
        if dataset_id == BRONZE_DATASET:
            return [RecordCountCheck(name="not_empty", min_rows=1),
                    SchemaCheck(name="schema", required=(("domain", "VARCHAR"), ("field", "VARCHAR"),
                                                         ("value", "VARCHAR"), ("method", "VARCHAR")))]
        return []

    def collect_metrics(self, ctx: StageContext) -> dict[str, float]:
        files = dataset_files("silver.domain_facts", {"source": "website", "country": ctx.params["country"]})
        if not files:
            return {}
        con = duck()
        try:
            row = con.execute(f"SELECT count(*), count(DISTINCT domain) FROM {parquet_source(files)}").fetchone()
        finally:
            con.close()
        return {"silver.domain_facts.facts": float(row[0]), "silver.domain_facts.domains": float(row[1])}
