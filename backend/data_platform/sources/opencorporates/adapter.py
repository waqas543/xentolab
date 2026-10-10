"""OpenCorporates source adapter: legal-entity records for organizations Gold already knows.

Boundary rules (OpenCorporates' terms): only the official API, only with the operator's own
API token (environment variable DP_SECRET_OPENCORPORATES_API_TOKEN — never a parameter,
never logged), within a per-run request budget, and stopping at the first quota response.
Nothing here scrapes the website or works around limits. Data is ODbL: attribution and
share-alike apply to anything published from it.

Each run searches the registry for the most significant Gold organizations (by number of
locations) and for companies known to Wikidata, skipping names searched within
`refresh_days`, and keeps every response verbatim.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
from datetime import date, datetime, timedelta, timezone

import pyarrow as pa

from ... import db, http, repository
from ...engine.context import StageContext
from ...errors import PermanentError, TransientError, ValidationError
from ...lake.reader import dataset_files, duck, parquet_source
from ...lake.writer import DatasetVersionWriter
from ...normalize import NORMALIZER_VERSION, name_key
from ...observability import get_logger
from ...quality.checks import RecordCountCheck, SchemaCheck
from ..base import DataSourceAdapter, DiscoveredDataset, PipelineSpec, SourceCapabilities, StageResult
from ..common import current_bronze, stage_file, store_artifact, verify_artifact
from . import silver

log = get_logger(__name__)

SECRET = "OPENCORPORATES_API_TOKEN"
BRONZE_DATASET = "bronze.opencorporates_companies"
BRONZE_SCHEMA = pa.schema([
    ("query", pa.string()), ("query_name_key", pa.string()), ("query_origin", pa.string()),
    ("searched_at", pa.timestamp("s", tz="UTC")), ("result_rank", pa.int16()),
    ("jurisdiction_code", pa.string()), ("company_number", pa.string()), ("name", pa.string()),
    ("company_type", pa.string()), ("current_status", pa.string()), ("incorporation_date", pa.string()),
    ("dissolution_date", pa.string()), ("registered_address", pa.string()), ("opencorporates_url", pa.string()),
    ("registry_url", pa.string()), ("company_json", pa.string()),
    ("source", pa.string()), ("artifact_id", pa.int64()), ("pipeline_run_id", pa.int64()),
    ("ingested_at", pa.timestamp("s", tz="UTC")),
])


def api_url() -> str:
    return os.environ.get("OPENCORPORATES_API_URL", "https://api.opencorporates.com/v0.4").rstrip("/")


def candidate_names(country: str, min_locations: int) -> list[dict]:
    """Organizations worth a registry lookup: multi-location Gold organizations, then Wikidata companies."""
    candidates: list[dict] = []
    try:
        with db.serving() as conn:
            for row in conn.execute("""
                SELECT name, organization_id FROM gold.organizations
                WHERE country_code = %s AND location_count >= %s ORDER BY location_count DESC, name
            """, [country, min_locations]).fetchall():
                candidates.append({"query": row["name"], "origin": f"gold:{row['organization_id']}"})
    except Exception:
        pass                                          # no Gold yet
    files = dataset_files("silver.organizations", {"source": "wikidata", "country": country})
    if files:
        con = duck()
        try:
            for name, legal, qid in con.execute(f"""
                SELECT name, legal_name, wikidata FROM {parquet_source(files)}
                WHERE entity_type = 'company' AND country_code = '{country}'
                ORDER BY coalesce(employees, 0) DESC, name
            """).fetchall():
                candidates.append({"query": legal or name, "origin": f"wikidata:{qid}"})
        finally:
            con.close()
    unique, seen = [], set()
    for candidate in candidates:
        key = name_key(candidate["query"])
        if key and len(key) >= 3 and key not in seen:
            seen.add(key)
            unique.append({**candidate, "name_key": key})
    return unique


class OpenCorporatesAdapter(DataSourceAdapter):
    role = "Company registry"
    bronze_accumulates = True
    source_id = "opencorporates"
    name = "OpenCorporates"
    description = "Company-registry records (legal name, number, status, incorporation date) via the official API."
    homepage = "https://opencorporates.com"
    license = "ODbL 1.0 (attribution + share-alike); API terms apply"
    capabilities = SourceCapabilities(supports_download=True, supports_incremental=True,
                                      provides_organizations=True, credentials=(SECRET,))
    parser_version = "opencorporates-bronze-1"
    transformation_version = f"opencorporates-silver-1+{NORMALIZER_VERSION}"

    def pipelines(self) -> list[PipelineSpec]:
        return [PipelineSpec(
            id="opencorporates.lookup",
            name="OpenCorporates registry lookup",
            description="Look up the country's most significant organizations in company registries "
                        "(requires your own API token in DP_SECRET_OPENCORPORATES_API_TOKEN).",
            parameters={
                "country": {"type": "string", "pattern": "^[A-Za-z]{2}$", "title": "Country",
                            "description": "Gold scope whose organizations are looked up, e.g. PK."},
                "jurisdiction_code": {"type": ["string", "null"], "default": None, "pattern": "^[a-z_]{2,8}$",
                                      "title": "Jurisdiction", "description": "Registry to search, e.g. gb or "
                                      "ae_du. Empty = any registry in the country."},
                "max_requests": {"type": "integer", "default": 50, "minimum": 1, "maximum": 1000,
                                 "title": "Request budget", "description": "API calls this run may make "
                                                                           "(stay within your plan)."},
                "min_locations": {"type": "integer", "default": 2, "minimum": 1, "title": "Min locations",
                                  "description": "Only organizations with at least this many Gold locations."},
                "refresh_days": {"type": "integer", "default": 90, "minimum": 1, "title": "Re-search after (days)"},
                "requests_per_second": {"type": "number", "default": 1, "minimum": 0.1, "maximum": 5,
                                        "title": "Request rate"},
            },
            required=["country"],
        )]

    def datasets(self) -> list[dict]:
        return [dict(dataset_id=BRONZE_DATASET, layer="bronze", name="OpenCorporates companies",
                     description="Company records returned by registry searches (raw JSON kept).",
                     source_id="opencorporates", partition_columns=["country", "artifact_id"])]

    def check_parameters(self, params: dict) -> dict:
        params["country"] = params["country"].upper()
        return params

    def concurrency_key(self, params: dict) -> str:
        return "opencorporates"                     # one API budget: never two lookups at once

    def run_labels(self, params: dict):
        return params["country"], f"registry lookup {params.get('jurisdiction_code') or ''}".strip()

    def _token(self, ctx: StageContext) -> str:
        token = ctx.settings.secret(SECRET)
        if not token:
            raise PermanentError(f"OpenCorporates needs an API token: set DP_SECRET_{SECRET} in the environment of "
                                 "the worker (free tokens for open-data / public-benefit use are available from "
                                 "opencorporates.com). Nothing was requested.")
        return token

    # -- discover / download / validate -------------------------------------------------

    def discover_available_datasets(self, ctx: StageContext) -> list[DiscoveredDataset]:
        self._token(ctx)
        country = ctx.params["country"]
        searched = repository.get_checkpoint(f"opencorporates:{country}", "searched") or {}
        cutoff = (date.today() - timedelta(days=ctx.params["refresh_days"])).isoformat()
        pending = [c for c in candidate_names(country, ctx.params["min_locations"])
                   if searched.get(c["name_key"], "") < cutoff]
        batch = pending[:ctx.params["max_requests"]]
        if not batch:
            raise PermanentError("Every candidate organization was searched recently; nothing to look up "
                                 f"(refresh_days={ctx.params['refresh_days']})")
        digest = hashlib.sha256(json.dumps([c["name_key"] for c in batch]).encode()).hexdigest()[:16]
        today = date.today().isoformat()
        return [DiscoveredDataset(
            dataset_name="registry_lookup", url=f"{api_url()}/companies/search", source_version=today,
            release_date=today, country=country, format="jsonl.gz",
            checksum=f"opencorporates:{country}:{today}:{digest}",
            metadata={"queries": batch, "pending_total": len(pending),
                      "jurisdiction_code": ctx.params.get("jurisdiction_code")},
        )]

    def download(self, ctx: StageContext) -> StageResult:
        token = self._token(ctx)
        discovered = ctx.get("discovered")
        country, meta = ctx.params["country"], discovered["metadata"]
        existing = repository.find_available_artifact(self.source_id, source_checksum=discovered["checksum"])
        if existing and not ctx.params["force_download"]:
            ctx.put(artifact_id=existing["id"])
            return StageResult.skipped(f"These lookups were already made today (artifact #{existing['id']})")
        filename = f"opencorporates-{country}-{discovered['source_version']}-{discovered['checksum'][-16:]}.jsonl.gz"
        artifact = repository.create_artifact(
            source_id=self.source_id, dataset_name="registry_lookup", country=country, source_url=discovered["url"],
            filename=filename, format="jsonl", compression="gzip", source_checksum=discovered["checksum"],
            source_version=discovered["source_version"], source_release_date=discovered["release_date"],
            download_started_at=repository.now(), pipeline_run_id=ctx.run_id,
            metadata={"queries": len(meta["queries"]), "jurisdiction_code": meta["jurisdiction_code"]})
        pacer = http.Pacer(1.0 / ctx.params["requests_per_second"])
        session = http.new_session()
        local = stage_file(ctx, filename)
        searched = repository.get_checkpoint(f"opencorporates:{country}", "searched") or {}
        made, results, quota_hit = 0, 0, None
        try:
            with gzip.open(local, "wt", encoding="utf-8") as out:
                for number, query in enumerate(meta["queries"]):
                    params = {"q": query["query"], "per_page": "5", "api_token": token}
                    if meta["jurisdiction_code"]:
                        params["jurisdiction_code"] = meta["jurisdiction_code"]
                    else:
                        params["country_code"] = country.lower()
                    try:
                        response = http.request("GET", f"{api_url()}/companies/search", params=params, pacer=pacer,
                                                session=session, secret_params=("api_token",), attempts=3,
                                                should_cancel=ctx.check_cancelled, retry_rate_limited=False)
                    except PermanentError as error:
                        status = (error.context or {}).get("status")
                        if status == 401:
                            raise PermanentError("OpenCorporates rejected the API token (HTTP 401)") from None
                        if status == 403:
                            quota_hit = "HTTP 403 (plan limit reached)"
                            break
                        raise
                    except TransientError as error:
                        if (error.context or {}).get("status") == 429:
                            quota_hit = "HTTP 429 (rate limit)"
                            break                     # stop at the first quota response; never hammer
                        raise
                    made += 1
                    body = response.json()
                    companies = body.get("results", {}).get("companies", [])
                    results += len(companies)
                    out.write(json.dumps({**query, "searched_at": datetime.now(timezone.utc).isoformat(),
                                          "response": body}, ensure_ascii=False) + "\n")
                    searched[query["name_key"]] = date.today().isoformat()
                    ctx.progress(number + 1, len(meta["queries"]), "requests", f"{results:,} company records")
        except Exception:
            repository.update_artifact(artifact["id"], status="failed")
            raise
        if made == 0:
            repository.update_artifact(artifact["id"], status="failed")
            raise PermanentError(f"OpenCorporates quota exhausted before any lookup: {quota_hit}")
        stored = store_artifact(ctx, artifact, local, requests=made, companies=results, quota_hit=quota_hit)
        repository.set_checkpoint(f"opencorporates:{country}", "searched", searched, ctx.run_id)
        if quota_hit:
            log.warning("stopped at the OpenCorporates quota", reason=quota_hit, requests=made)
        return StageResult(records_in=made, records_out=results, bytes_processed=stored["size_bytes"],
                           metrics={"requests": made, "companies": results, "quota_hit": quota_hit})

    def validate_download(self, artifact: dict, ctx: StageContext) -> StageResult:
        path = verify_artifact(ctx, artifact)
        lines = 0
        try:
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                for raw in handle:
                    record = json.loads(raw)
                    if "response" not in record or "query" not in record:
                        raise ValidationError("Lookup line without query/response")
                    lines += 1
        except (OSError, json.JSONDecodeError) as error:
            raise ValidationError(f"Unreadable lookup file: {error}") from error
        return StageResult(records_in=lines, records_out=lines)

    # -- bronze / silver --------------------------------------------------------------------

    def ingest_bronze(self, artifact: dict, ctx: StageContext) -> StageResult:
        existing = current_bronze(BRONZE_DATASET, artifact["id"], self.parser_version)
        if existing and not ctx.params["rebuild_bronze"]:
            ctx.put(bronze_versions={BRONZE_DATASET: existing["id"]})
            return StageResult.skipped(f"Bronze already exists for artifact #{artifact['id']}",
                                       records_out=existing["row_count"])
        now = datetime.now(timezone.utc).replace(microsecond=0)
        columns = {f.name: [] for f in BRONZE_SCHEMA}
        with gzip.open(ctx.storage.local_path(artifact["storage_key"]), "rt", encoding="utf-8") as handle:
            for raw in handle:
                record = json.loads(raw)
                searched_at = datetime.fromisoformat(record["searched_at"]).replace(microsecond=0)
                for rank, item in enumerate(record["response"].get("results", {}).get("companies", [])):
                    company = item.get("company", item)
                    for key, value in (("query", record["query"]), ("query_name_key", record["name_key"]),
                                       ("query_origin", record.get("origin")), ("searched_at", searched_at),
                                       ("result_rank", rank), ("jurisdiction_code", company.get("jurisdiction_code")),
                                       ("company_number", company.get("company_number")),
                                       ("name", company.get("name")), ("company_type", company.get("company_type")),
                                       ("current_status", company.get("current_status")),
                                       ("incorporation_date", company.get("incorporation_date")),
                                       ("dissolution_date", company.get("dissolution_date")),
                                       ("registered_address", company.get("registered_address_in_full")),
                                       ("opencorporates_url", company.get("opencorporates_url")),
                                       ("registry_url", company.get("registry_url")),
                                       ("company_json", json.dumps(company, ensure_ascii=False)),
                                       ("source", "opencorporates"), ("artifact_id", artifact["id"]),
                                       ("pipeline_run_id", ctx.run_id), ("ingested_at", now)):
                        columns[key].append(value)
        with DatasetVersionWriter(ctx.storage, BRONZE_DATASET,
                                  {"country": artifact["country"], "artifact_id": artifact["id"]}, BRONZE_SCHEMA,
                                  run_id=ctx.run_id, versions={"parser": self.parser_version},
                                  inputs=[{"type": "artifact", "id": artifact["id"], "sha256": artifact["sha256"]}]) \
                as writer:
            writer.write_batch(pa.table(columns, schema=BRONZE_SCHEMA))
        ctx.put(bronze_versions={BRONZE_DATASET: writer.version["id"]})
        return StageResult(records_out=writer.row_count, bytes_processed=artifact["size_bytes"])

    def transform_silver(self, ctx: StageContext) -> StageResult:
        country = ctx.params["country"]
        bronze = [v for v in repository.current_versions(BRONZE_DATASET, {"country": country})
                  if v["versions"].get("parser") == self.parser_version]
        if not bronze:
            raise PermanentError(f"No OpenCorporates Bronze for {country}")
        partition = {"source": "opencorporates", "country": country}
        current = repository.current_versions("silver.organizations", partition)
        if current and not ctx.params["rebuild_silver"]:
            built_from = {i["id"] for i in current[0]["inputs"] if i.get("dataset_id") == BRONZE_DATASET}
            if built_from == {v["id"] for v in bronze} and \
                    current[0]["versions"].get("transformation") == self.transformation_version:
                ctx.put(silver_versions={"silver.organizations": current[0]["id"]})
                return StageResult.skipped("Silver is current for these lookups")
        version, stats = silver.transform(ctx, country, bronze, self.transformation_version)
        ctx.put(silver_versions={"silver.organizations": version["id"]})
        return StageResult(records_in=stats["records"], records_out=version["row_count"], metrics=stats)

    # -- quality & metrics --------------------------------------------------------------------

    def quality_checks(self, layer: str, dataset_id: str):
        if dataset_id == BRONZE_DATASET:
            return [RecordCountCheck(name="results_present", min_rows=1, severity="warn"),
                    SchemaCheck(name="schema", required=(("company_number", "VARCHAR"),
                                                         ("jurisdiction_code", "VARCHAR")))]
        return []

    def collect_metrics(self, ctx: StageContext) -> dict[str, float]:
        files = dataset_files("silver.organizations", {"source": "opencorporates", "country": ctx.params["country"]})
        if not files:
            return {}
        con = duck()
        try:
            row = con.execute(f"""
                SELECT count(*), count(*) FILTER (WHERE status ILIKE '%active%') FROM {parquet_source(files)}
            """).fetchone()
        finally:
            con.close()
        return {"silver.organizations.registry_records": float(row[0] or 0),
                "silver.organizations.active": float(row[1] or 0)}
