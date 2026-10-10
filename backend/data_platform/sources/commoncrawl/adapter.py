"""Common Crawl source adapter: web-domain discovery from the crawl's URL index.

Domain discovery only — page content (WARC records) is a separate concern: Silver keeps
each domain's homepage WARC reference (file, offset, length) so selected pages can be
fetched later without searching the index again.

Scope per run:
  * every capture under the country's ccTLD (e.g. *.pk) — finds domains no other source knows;
  * the busiest domains already known from Gold businesses outside the ccTLD (e.g. .com sites
    of Pakistani companies), looked up by key range in the same index.
The artifact keeps the fetched index blocks byte-for-byte (a valid multi-member gzip file).
"""

from __future__ import annotations

import gzip
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pyarrow as pa

from ... import db, http, repository
from ...engine.context import StageContext
from ...errors import PermanentError, ValidationError
from ...lake.reader import dataset_files, duck, parquet_source
from ...lake.writer import DatasetVersionWriter
from ...normalize import NORMALIZER_VERSION, surt_host_prefix
from ...observability import get_logger
from ...quality.checks import RecordCountCheck, SchemaCheck
from ..base import DataSourceAdapter, DiscoveredDataset, PipelineSpec, SourceCapabilities, StageResult
from ..common import current_bronze, reuse_artifact, silver_is_current, stage_file, store_artifact, verify_artifact
from . import index, silver

log = get_logger(__name__)

BRONZE_DATASET = "bronze.commoncrawl_captures"
BRONZE_SCHEMA = pa.schema([
    ("urlkey", pa.string()), ("captured_at", pa.timestamp("s", tz="UTC")), ("url", pa.string()),
    ("host", pa.string()), ("mime", pa.string()), ("mime_detected", pa.string()), ("status", pa.int32()),
    ("digest", pa.string()), ("length", pa.int64()), ("offset", pa.int64()), ("filename", pa.string()),
    ("languages", pa.string()), ("charset", pa.string()), ("redirect", pa.string()), ("truncated", pa.string()),
    ("crawl", pa.string()), ("scope", pa.string()),
    ("source", pa.string()), ("artifact_id", pa.int64()), ("pipeline_run_id", pa.int64()),
    ("ingested_at", pa.timestamp("s", tz="UTC")),
])


def known_domains(country: str, tld: str, limit: int) -> list[str]:
    """Own domains of Gold businesses in the country outside its ccTLD, busiest first."""
    if limit <= 0:
        return []
    try:
        with db.serving() as conn:
            rows = conn.execute("""
                SELECT domain FROM gold.domains
                WHERE country_code = %s AND business_count > 0 AND tld IS DISTINCT FROM %s
                  AND domain NOT LIKE %s
                ORDER BY business_count DESC, organization_count DESC, domain LIMIT %s
            """, [country, tld, f"%.{tld}", limit]).fetchall()
    except Exception:              # no Gold yet (or table not migrated): ccTLD scope only
        return []
    return [r["domain"] for r in rows]


class ScopeMatcher:
    """Does an index key belong to this run's scope (the ccTLD or one of the known domains)?"""

    def __init__(self, tld: str, domains: list[str]):
        self.tld_prefix = f"{tld},"
        self.domains = {surt_host_prefix(d): d for d in domains}

    def scope(self, key: str) -> str | None:
        host = key.split(")", 1)[0]
        if host.startswith(self.tld_prefix) or host == self.tld_prefix[:-1]:
            return "tld"
        while host:
            if host in self.domains:
                return "known_domain"
            host = host.rpartition(",")[0]
        return None


def parse_line(raw: str) -> tuple[str, str, dict] | None:
    key, _, rest = raw.partition(" ")
    timestamp, _, payload = rest.partition(" ")
    if not payload.startswith("{"):
        return None
    try:
        return key, timestamp, json.loads(payload)
    except json.JSONDecodeError:
        return None


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class CommonCrawlAdapter(DataSourceAdapter):
    role = "Web domains"
    source_id = "commoncrawl"
    name = "Common Crawl"
    description = "Open web crawl: discovers the country's web domains (ccTLD) and how they are crawled."
    homepage = "https://commoncrawl.org"
    license = "Common Crawl Terms of Use (free)"
    capabilities = SourceCapabilities(supports_download=True, supports_release_detection=True)
    parser_version = "commoncrawl-bronze-1"
    transformation_version = f"commoncrawl-silver-2+{NORMALIZER_VERSION}"   # 2: capture_coverage

    def pipelines(self) -> list[PipelineSpec]:
        return [PipelineSpec(
            id="commoncrawl.domains",
            name="Common Crawl domain discovery",
            description="Read the crawl's URL index for one country's ccTLD (and known business domains) and "
                        "derive per-domain crawl statistics. Page content is not downloaded.",
            parameters={
                "country": {"type": "string", "pattern": "^[A-Za-z]{2}$", "title": "Country",
                            "description": "ISO 3166-1 alpha-2 code, e.g. PK."},
                "tld": {"type": ["string", "null"], "default": None, "pattern": "^[a-z0-9-]{2,24}$",
                        "title": "Top-level domain", "description": "Empty = the country's ccTLD (pk for PK)."},
                "crawl": {"type": ["string", "null"], "default": None, "pattern": "^CC-MAIN-\\d{4}-\\d{2}$",
                          "title": "Crawl", "description": "e.g. CC-MAIN-2026-39. Empty = newest."},
                "max_known_domains": {"type": "integer", "default": 100, "minimum": 0, "maximum": 2000,
                                      "title": "Known domains outside the ccTLD",
                                      "description": "Busiest Gold business domains (e.g. .com) to look up too."},
                "max_blocks": {"type": ["integer", "null"], "default": None, "minimum": 1,
                               "title": "Max index blocks", "description": "Limit for trial runs (~3,000 captures "
                                                                           "and ~280 KB per block). Empty = all."},
                "parallel_requests": {"type": "integer", "default": 4, "minimum": 1, "maximum": 8,
                                      "title": "Parallel downloads"},
            },
            required=["country"],
        )]

    def datasets(self) -> list[dict]:
        return [dict(dataset_id=BRONZE_DATASET, layer="bronze", name="Common Crawl captures",
                     description="URL-index records (one per captured URL) with WARC references.",
                     source_id="commoncrawl", partition_columns=["country", "artifact_id"])]

    def check_parameters(self, params: dict) -> dict:
        params["country"] = params["country"].upper()
        params["tld"] = (params.get("tld") or params["country"]).lower()
        return params

    def concurrency_key(self, params: dict) -> str:
        return f"commoncrawl:{params['country']}"

    def run_labels(self, params: dict):
        return params["country"], f".{params.get('tld') or params['country'].lower()} {params.get('crawl') or 'latest'}"

    @staticmethod
    def _checksum(crawl: str, params: dict) -> str:
        return (f"commoncrawl:{crawl}:{params['country']}:{params['tld']}:known={params['max_known_domains']}"
                f":blocks={params.get('max_blocks') or 'all'}")

    def latest_release(self, params: dict) -> DiscoveredDataset | None:
        params = self.check_parameters(dict(params))
        params.setdefault("max_known_domains", 100)
        crawl = index.get_crawl(params.get("crawl"))
        return DiscoveredDataset(dataset_name="domains", url=None, source_version=crawl,
                                 checksum=self._checksum(crawl, params))

    # -- discover / download / validate -------------------------------------------------

    def discover_available_datasets(self, ctx: StageContext) -> list[DiscoveredDataset]:
        params = ctx.params
        crawl = index.get_crawl(params.get("crawl"))
        cluster = index.ClusterIndex(crawl)
        ctx.progress(0, None, "reads", f"locating .{params['tld']} in the {crawl} index", force=True)
        blocks = cluster.blocks_for_prefix(f"{params['tld']},")
        if not blocks:
            raise PermanentError(f"{crawl} has no captures under .{params['tld']}")
        domains = known_domains(params["country"], params["tld"], params["max_known_domains"])
        ranges = [{"scope": "tld", "lower": f"{params['tld']},", "upper": f"{params['tld']}-"}]
        seen = {(b.filename, b.offset) for b in blocks}
        sampled = []
        for number, domain in enumerate(domains):
            lower, upper = index.domain_range(domain)
            ranges.append({"scope": "known_domain", "domain": domain, "lower": lower, "upper": upper})
            # Known domains can be huge sites (a chain's global website): read the first block of the domain
            # and the block holding its www homepage, not the whole site.
            found = cluster.blocks_for_prefix(lower, upper)
            www_lower, www_upper = index.domain_range(f"www.{domain}")
            if len(found) > 2:
                sampled.append(domain)
            picks = [index.block_containing(found, lower + "/"),
                     index.block_containing(cluster.blocks_for_prefix(www_lower, www_upper), www_lower + "/")]
            for block in picks:
                if block is not None and (block.filename, block.offset) not in seen:
                    seen.add((block.filename, block.offset))
                    blocks.append(block)
            ctx.progress(number + 1, len(domains), "domains", f"locating known domains ({cluster.reads} reads)")
        if params.get("max_blocks"):
            blocks = blocks[:params["max_blocks"]]
        return [DiscoveredDataset(
            dataset_name="domains", url=cluster.url, source_version=crawl, release_date=None,
            size_bytes=sum(b.length for b in blocks), checksum=self._checksum(crawl, params),
            country=params["country"], format="cdx.gz",
            metadata={"crawl": crawl, "tld": params["tld"], "known_domains": domains, "sampled_domains": sampled,
                      "ranges": ranges,
                      "blocks": [[b.filename, b.offset, b.length, b.key] for b in blocks],
                      "index_reads": cluster.reads},
        )]

    def download(self, ctx: StageContext) -> StageResult:
        discovered = ctx.get("discovered")
        existing = reuse_artifact(ctx, self.source_id, discovered["checksum"])
        if existing:
            return StageResult.skipped(f"{discovered['source_version']} index blocks already stored "
                                       f"(artifact #{existing['id']})")
        meta = discovered["metadata"]
        crawl, country = meta["crawl"], ctx.params["country"]
        blocks = [index.Block(key, filename, offset, length, 0) for filename, offset, length, key in meta["blocks"]]
        ctx.storage.ensure_free_space(3 * discovered["size_bytes"], ctx.settings.min_free_disk_bytes)
        filename = f"commoncrawl-{crawl}-{country}-{meta['tld']}.cdx.gz"
        artifact = repository.create_artifact(
            source_id=self.source_id, dataset_name="domains", country=country, source_url=discovered["url"],
            filename=filename, format="cdx", compression="gzip", source_checksum=discovered["checksum"],
            source_version=crawl, download_started_at=repository.now(), pipeline_run_id=ctx.run_id,
            metadata={k: v for k, v in meta.items() if k != "blocks"} | {"block_count": len(blocks)})
        cluster_url = discovered["url"]
        base = cluster_url.rsplit("/", 1)[0]
        session = http.new_session()
        local = stage_file(ctx, filename)
        done = received = 0

        def fetch(block: index.Block) -> bytes:
            ctx.check_cancelled()
            return index.fetch_block(f"{base}/{block.filename}", block, session=session)

        try:
            # Blocks are written in index order, so the artifact itself stays sorted by key.
            with open(local, "wb") as out, ThreadPoolExecutor(ctx.params["parallel_requests"]) as pool:
                for raw in pool.map(fetch, blocks):
                    out.write(raw)
                    done += 1
                    received += len(raw)
                    ctx.progress(done, len(blocks), "blocks", f"{received / 1e6:,.0f} MB of index blocks")
        except Exception:
            repository.update_artifact(artifact["id"], status="failed")
            raise
        stored = store_artifact(ctx, artifact, local, blocks=len(blocks))
        return StageResult(records_out=len(blocks), bytes_processed=stored["size_bytes"],
                           metrics={"blocks": len(blocks), "bytes": received})

    def validate_download(self, artifact: dict, ctx: StageContext) -> StageResult:
        path = verify_artifact(ctx, artifact)
        lines = bad = 0
        try:
            with gzip.open(path, "rt", encoding="utf-8", errors="replace") as handle:
                for raw in handle:
                    lines += 1
                    if parse_line(raw.rstrip("\n")) is None:
                        bad += 1
                    if lines % 200_000 == 0:
                        ctx.progress(lines, None, "lines", "checking index lines")
        except (OSError, EOFError) as error:
            raise ValidationError(f"Corrupt index blocks: {error}") from error
        if lines == 0 or bad > lines * 0.01:
            raise ValidationError(f"{bad:,} of {lines:,} index lines are unreadable")
        return StageResult(records_in=lines, records_out=lines - bad, metrics={"lines": lines, "unreadable": bad})

    # -- bronze / silver --------------------------------------------------------------------

    def ingest_bronze(self, artifact: dict, ctx: StageContext) -> StageResult:
        existing = current_bronze(BRONZE_DATASET, artifact["id"], self.parser_version)
        if existing and not ctx.params["rebuild_bronze"]:
            ctx.put(bronze_versions={BRONZE_DATASET: existing["id"]})
            return StageResult.skipped(f"Bronze already exists for artifact #{artifact['id']}",
                                       records_out=existing["row_count"])
        meta = artifact["metadata"]
        matcher = ScopeMatcher(meta["tld"], meta.get("known_domains") or [])
        now = datetime.now(timezone.utc).replace(microsecond=0)
        partition = {"country": artifact["country"], "artifact_id": artifact["id"]}
        columns = {f.name: [] for f in BRONZE_SCHEMA}
        lines = kept = 0
        with DatasetVersionWriter(ctx.storage, BRONZE_DATASET, partition, BRONZE_SCHEMA, run_id=ctx.run_id,
                                  versions={"parser": self.parser_version},
                                  inputs=[{"type": "artifact", "id": artifact["id"], "sha256": artifact["sha256"]}]) \
                as writer, gzip.open(ctx.storage.local_path(artifact["storage_key"]), "rt", encoding="utf-8",
                                     errors="replace") as handle:
            for raw in handle:
                lines += 1
                parsed = parse_line(raw.rstrip("\n"))
                if parsed is None:
                    continue
                key, timestamp, record = parsed
                scope = matcher.scope(key)
                if scope is None:
                    continue                    # edge blocks also hold neighbouring keys
                url = record.get("url") or ""
                host = url.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0].lower() or None
                try:
                    captured = datetime.strptime(timestamp, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
                except ValueError:
                    captured = None
                for name, value in (("urlkey", key), ("captured_at", captured), ("url", url), ("host", host),
                                    ("mime", record.get("mime")), ("mime_detected", record.get("mime-detected")),
                                    ("status", _int(record.get("status"))), ("digest", record.get("digest")),
                                    ("length", _int(record.get("length"))), ("offset", _int(record.get("offset"))),
                                    ("filename", record.get("filename")), ("languages", record.get("languages")),
                                    ("charset", record.get("charset") or record.get("encoding")),
                                    ("redirect", record.get("redirect")), ("truncated", record.get("truncated")),
                                    ("crawl", meta["crawl"]), ("scope", scope), ("source", "commoncrawl"),
                                    ("artifact_id", artifact["id"]), ("pipeline_run_id", ctx.run_id),
                                    ("ingested_at", now)):
                    columns[name].append(value)
                kept += 1
                if kept % 100_000 == 0:
                    writer.write_batch(pa.table(columns, schema=BRONZE_SCHEMA))
                    columns = {f.name: [] for f in BRONZE_SCHEMA}
                    ctx.progress(lines, None, "lines", f"{kept:,} captures in scope")
            writer.write_batch(pa.table(columns, schema=BRONZE_SCHEMA))
        ctx.put(bronze_versions={BRONZE_DATASET: writer.version["id"]})
        return StageResult(records_in=lines, records_out=kept, records_rejected=lines - kept,
                           bytes_processed=artifact["size_bytes"], metrics={"out_of_scope_lines": lines - kept})

    def transform_silver(self, ctx: StageContext) -> StageResult:
        artifact_id = ctx.get("artifact_id") or ctx.params.get("artifact_id")
        artifact = repository.get_artifact(artifact_id) if artifact_id else self.latest_artifact(ctx.params)
        if artifact is None:
            raise PermanentError("No Common Crawl artifact available to build Silver from")
        bronze = current_bronze(BRONZE_DATASET, artifact["id"], self.parser_version)
        if bronze is None:
            raise PermanentError(f"Bronze is missing for artifact #{artifact['id']}; start from BRONZE_INGEST")
        partition = {"source": "commoncrawl", "country": artifact["country"]}
        current = silver_is_current(ctx, ["silver.web_domains"], partition, {bronze["id"]},
                                    self.transformation_version)
        if current:
            ctx.put(silver_versions=current)
            return StageResult.skipped("Silver is current for this Bronze and transformation version")
        version, stats = silver.transform(ctx, artifact, bronze, self.transformation_version)
        ctx.put(silver_versions={"silver.web_domains": version["id"]})
        return StageResult(records_in=stats["captures"], records_out=version["row_count"], metrics=stats)

    # -- quality & metrics --------------------------------------------------------------------

    def quality_checks(self, layer: str, dataset_id: str):
        if dataset_id == BRONZE_DATASET:
            return [RecordCountCheck(name="not_empty", min_rows=1),
                    SchemaCheck(name="schema", required=(("urlkey", "VARCHAR"), ("url", "VARCHAR"),
                                                         ("filename", "VARCHAR"), ("offset", "BIGINT")))]
        return []

    def collect_metrics(self, ctx: StageContext) -> dict[str, float]:
        files = dataset_files("silver.web_domains", {"source": "commoncrawl", "country": ctx.params["country"]})
        if not files:
            return {}
        con = duck()
        try:
            row = con.execute(f"""
                SELECT count(*), count(*) FILTER (WHERE is_business_candidate), sum(captures),
                       count(*) FILTER (WHERE homepage_status BETWEEN 200 AND 299)
                FROM {parquet_source(files)}
            """).fetchone()
        finally:
            con.close()
        return dict(zip(["silver.web_domains.domains", "silver.web_domains.business_candidates",
                         "silver.web_domains.captures", "silver.web_domains.live_homepages"],
                        [float(v or 0) for v in row]))
