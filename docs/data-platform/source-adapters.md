# Source adapters

A source is a plugin: a subclass of `DataSourceAdapter` (`data_platform/sources/base.py`). The engine only talks to
adapters through the registry (`sources/registry.py`). Built-ins are listed in `BUILTIN_ADAPTERS`; external plugins
are loaded with `DATA_PLATFORM_EXTRA_SOURCES="pkg.module:Class"`, so adding a source never changes the engine.

```python
class DataSourceAdapter(ABC):
    source_id: str                      # "osm"
    capabilities: SourceCapabilities    # supports_download / incremental / release_detection, provides_places …
    parser_version: str                 # bump when Bronze parsing changes
    transformation_version: str         # bump when Silver normalization changes

    def pipelines(self) -> list[PipelineSpec]           # ids, JSON-schema parameters, stage list
    def datasets(self) -> list[dict]                    # Bronze/Silver datasets for the catalog
    def concurrency_key(self, params) -> str            # runs with the same key never overlap
    def check_parameters(self, params) -> dict          # cross-field validation / normalization
    def latest_release(self, params) -> DiscoveredDataset | None   # cheap "new version?" check for schedules

    def discover_available_datasets(self, ctx) -> list[DiscoveredDataset]
    def download(self, ctx) -> StageResult              # store artifact, ctx.put(artifact_id=…)
    def validate_download(self, artifact, ctx) -> StageResult
    def ingest_bronze(self, artifact, ctx) -> StageResult
    def transform_silver(self, ctx) -> StageResult      # write the shared Silver contract
    def build_gold(self, ctx) -> StageResult | None     # optional, source-specific extras
    def quality_checks(self, layer, dataset_id) -> list[QualityCheck]
    def collect_metrics(self, ctx) -> dict[str, float]
```

Compared with the interface sketched in the brief, entity resolution and Gold are **platform** stages rather than
adapter methods. Gold is inherently cross-source, and keeping it in the platform means a new adapter only has to
produce valid Silver.

## What a stage receives (`StageContext`)

- `ctx.params`: validated parameters (common ones plus the adapter's own)
- `ctx.storage`, `ctx.settings` (and `settings.secret(name)` for credentials from `DP_SECRET_<NAME>`)
- `ctx.put(**values)` / `ctx.get(key)`: shared run state persisted to `run.outputs` (for example `artifact_id`,
  `bronze_versions`)
- `ctx.progress(current, total=None, unit, message)`: throttled progress, which is also the cancellation point
- `ctx.on_cleanup(fn)`: called on failure or cancellation

## Parameters

`PipelineSpec.parameters` are JSON-Schema properties. The common parameters are `force_download`, `rebuild_*`,
`start_stage`, `artifact_id` and `er_algorithm`; they are added automatically. The frontend renders the run form from
this schema, so a new adapter needs no UI code. Parameter names that look like credentials are rejected: secrets must
come from the environment.

## The OSM adapter

| Stage | Behavior |
|---|---|
| Discover | `source_path` (a local file) or Geofabrik's index (`country` → region; `.md5`, `Last-Modified`) |
| Download | Local: SHA-256, then a hard link. Remote: resumable HTTP `Range` download with streaming SHA-256/MD5, verified against the provider MD5. |
| Validate | Re-hash; read the PBF header (bbox, replication timestamp/sequence/URL); record the replication checkpoint for future incremental diffs |
| Bronze | pyosmium streaming into three Parquet datasets in 200k-row batches. Bounded memory, nothing filtered. |
| Silver | DuckDB: business candidates (the SQL mirror of `taxonomy.is_osm_business`), way and relation centroids from Bronze nodes, boundary rings. Python: normalizers and geography on 50k-row batches. |

Measured on Pakistan:
- Bronze took 27.1M objects in 6.6 min.
- Silver took 70k places in 29 s.
- Entity resolution took 1 s.
- Gold took 2 s.
- Publish took 4 s.

The Python here is an x86_64 build running under Rosetta, and a native arm64 Python should be noticeably faster.

## The Overture adapter (Milestone 2)

The pipeline is `overture.places`. Its parameters are `country` and an optional `release` (default: latest).

| Stage | Behavior |
|---|---|
| Discover | Official STAC catalog (`stac.overturemaps.org`): latest or chosen release, places collection, and the country bbox from Overture's own divisions theme (cached per release in `dp.pipeline_checkpoints`) |
| Download | Reads only the place files whose STAC bbox intersects the country (plus 1° margin), and within them only the **row groups** whose bbox statistics intersect. 8 parallel HTTPS range readers. Rows are kept where `addresses[0].country = <country>`. Progress is real (row groups done / total). |
| Validate | Re-hash; required columns (`id`, `geometry`, `names`, `bbox`, `addresses`); new provider columns are logged and kept |
| Bronze | The artifact rows with **every provider column** (nested structs, `sources` with the underlying Meta/Microsoft/… record ids) plus lineage columns |
| Silver | DuckDB flattens the structs, falling back to older `categories` when `taxonomy` is absent; `taxonomy.classify_overture` maps to the shared groups and drops non-businesses with the same rules as OSM; shared normalizers; geography from the shared `silver.geo_boundaries` / `silver.geo_places`, falling back to the address fields |

Notes:
- **Reading from the public bucket.** Overture publishes in public, anonymous cloud buckets. The platform only reads
  from them over HTTPS; everything it keeps is local.
- **Tests.** `OVERTURE_STAC_URL` can point at a local catalog (`file://…`), which is how the tests run a complete
  fixture release offline.
- **Idempotency.** The provider checksum is `overture:<release>:places:<country>`, so re-running a release for the
  same country skips the download.
- **Geography changes.** Silver is rebuilt when the Bronze version, the transformation version **or the shared
  geography versions** change. A new OSM extract therefore refreshes Overture's city assignment too.

## Shared building blocks (`sources/common.py`)

Every adapter written after Overture composes the same helpers instead of re-implementing them:

- `stage_file`: a private staging path, cleaned up automatically if the stage fails or is cancelled.
- `store_artifact`: hashes the file, stores it without overwriting (hardlink when possible) and marks it available.
- `reuse_artifact`: skips the download when an artifact with the same provider checksum exists.
- `verify_artifact`: re-hashes the stored file in VALIDATE.
- `lineage_sql`: the lineage columns every Bronze record carries.
- `current_bronze` and `silver_is_current`: the idempotency checks.

API-style providers use `http.request`, which provides:

- in-request retries for 429/5xx with `Retry-After`;
- a `Pacer` for polite request rates;
- `secret_params` so credentials never appear in errors;
- `retry_rate_limited=False` for quota-metered APIs.

`http.get_range` handles HTTP range reads.

## The GeoNames adapter (Milestone 3)

The pipeline is `geonames.country` (`country`, optional alternate names and postal codes). GeoNames is the platform's
**geographic reference dimension**.

| Stage | Behavior |
|---|---|
| Discover | HEAD of the official daily dumps: `dump/<CC>.zip`, `alternatenames/<CC>.zip`, `zip/<CC>.zip` (postal codes), admin1/admin2 code tables, `countryInfo.txt`. The provider checksum is a hash of their ETags, sizes and dates, so an unchanged snapshot is never downloaded twice. |
| Download | All files go into **one bundle artifact** (stored zip) with a `manifest.json` of per-file SHA-256/MD5, ETag and Last-Modified |
| Validate | Every member against the manifest; the column count of the main dump |
| Bronze | `bronze.geonames_places` (every feature), `_alternate_names`, `_admin_codes`, `_postal_codes`, `_countries`. Each is loaded by DuckDB from the original tab-separated text, with no quoting, because names contain quote characters. |
| Silver | `silver.geo_places`: every populated place (city / town / village / suburb from feature code and population), with Urdu names, admin names, alternate names and Wikidata links. Historical and abandoned places are excluded. `silver.postal_codes`. Business-like **facilities** (hotels, hospitals, colleges, mills, factories, markets…: `taxonomy.GEONAMES_FEATURES`) go to `silver.places` with their Wikidata id. |

**Geography precedence.** `silver_contract.GEO_PLACE_PRECEDENCE = ["geonames", "osm"]`. Once GeoNames is loaded for
a country:

- Every source's city and locality come from the GeoNames gazetteer. For Pakistan that is 150k places instead of the
  ~400 towns OSM tags.
- Boundary polygons still come from OSM.
- Where no polygon covers a point, the nearest gazetteer place supplies the admin names.

Silver records which geography versions it used, so a geography change makes the next run of each source rebuild its
Silver. You can also backfill it from `SILVER_TRANSFORM`.

## The Wikidata adapter (Milestone 3)

The pipeline is `wikidata.organizations`. Its parameters are:

- `country`;
- `scope`: `organizations` (default; includes schools, universities and agencies) or `businesses`;
- `include_referenced`;
- `max_entities`;
- `parallel_requests` (default 2, as Wikimedia's etiquette asks).

| Stage | Behavior |
|---|---|
| Discover | SPARQL: the country item (by ISO code), then the classes its items use (~2k for PK), then which of those are subclasses of *organization* (or *business*). The tree is walked upward with a forward-gearing hint: walking it down from *organization* times out. |
| Download | **Listing.** Query plans matter on the public service, which stops queries after 60 s:<br>• Large classes that exist only in this country (Pakistan has ~26k "Government Boys' Primary School" items) are listed **class-first** with the optimizer off.<br>• The ~500 small classes share **one country-first query** filtered by class.<br>• Generic classes ("organization") are never listed class-first: they have millions of instances worldwide.<br>• Revision ids come from direct lookups in batches of 400.<br>Brands and entities that OSM and Overture tag (`brand:wikidata`, `wikidata`) are added.<br>**Incremental:** entities whose revision is unchanged are copied from the previous snapshot; only new or edited ones are fetched via `wbgetentities` (50 per request, `maxlag=5`, gzip). Labels of referenced items (classes, industries, cities, parents) are fetched once and reused. A listing identical to a stored snapshot is not stored again. |
| Validate | Every line is a JSON entity; the count matches |
| Bronze | `bronze.wikidata_entities`: the **full entity JSON** per item, plus QID, role, revision, labels and lineage |
| Silver | `silver.organizations`, extracted from claims: types, industries, website and domain, headquarters, coordinates, inception, dissolution, parent and subsidiaries, founders, brands, employees, legal name, and identifiers (LEI, ISIN, OpenCorporates ID, social handles…) |

## The Common Crawl adapter (Milestone 4)

The pipeline is `commoncrawl.domains`. Its parameters are:

- `country`;
- `tld` (default: the ccTLD);
- `crawl` (default: newest);
- `max_known_domains` (default 100);
- `max_blocks`;
- `parallel_requests`.

It does **domain discovery only**. It never downloads the corpus, and page content is a separate, later step.

| Stage | Behavior |
|---|---|
| Discover | Newest crawl from the public collections listing. **Binary search of `cluster.idx` with HTTP range requests** (~30 reads of 4 KB) finds the index blocks under the ccTLD (`pk,…` in SURT order). For the busiest Gold business domains outside the ccTLD (e.g. `.com` sites of Pakistani companies), only two blocks are read: the domain's first block and the block holding its `www` homepage. A chain's global site (shell.com) can span dozens of blocks, so those domains are marked `capture_coverage = 'sample'`. |
| Download | Only those blocks (~280 KB, ~3,000 captures each), fetched in parallel and kept **byte-for-byte** as a multi-member gzip artifact |
| Validate | Every line parses as `SURT timestamp {json}` |
| Bronze | `bronze.commoncrawl_captures`: one row per captured URL, including the **WARC reference** (file, offset, length) |
| Silver | `silver.web_domains`: one row per registrable domain (public-suffix list, offline). It records hosts, pages, first and last capture, the status mix, languages, homepage (status, redirect target) and the homepage's WARC reference for later page extraction. It also records `domain_type` from the second-level label (`com.pk` commercial, `edu.pk` education, `gov.pk` government…) and `is_business_candidate` (commercial, live, not spam-like). |

Only static files on `data.commoncrawl.org` are used. The CDX query server (`index.commoncrawl.org`) is often
overloaded and isn't needed.

## The GDELT adapter (Milestone 5)

The pipeline is `gdelt.mentions`. Its parameters are `country`, `hours` (default 24), `include_translation`,
`retention_days` and `parallel_requests`. GDELT is an **enrichment and discovery signal**, not a registry.

| Stage | Behavior |
|---|---|
| Discover | Latest 15-minute file from `lastupdate.txt`. **Incremental:** the window starts after the checkpoint `gdelt:<CC>/last_file` (first run: the last `hours`), at most `hours` per run. |
| Download | Each GKG 2.1 file (English and machine-translated) is streamed and unzipped in memory. Only records with a location in the country (GDELT uses **FIPS** codes, mapped from GeoNames' `countryInfo`) or from an outlet under the ccTLD are kept, unchanged and prefixed with feed and file. Missing intervals (GDELT skips some) are counted, not fatal. |
| Bronze | `bronze.gdelt_gkg`: all 27 GKG columns plus `published_at`. The checkpoint advances **only once a window is in Bronze**. |
| Silver | `silver.org_mentions`: organizations per normalized name across all stored windows within `retention_days`, with mentions, articles, outlets, first and last seen, tone, top themes, top places and sample URLs. Wire services and platforms are excluded as noise. |

## The OpenCorporates adapter (Milestone 5)

The pipeline is `opencorporates.lookup`. Its parameters are:

- `country`;
- `jurisdiction_code`;
- `max_requests` (default 50);
- `min_locations`;
- `refresh_days`;
- `requests_per_second`.

The adapter boundary respects OpenCorporates' terms:

- **Official API only, with your own token.** The token comes from the worker's environment
  (`DP_SECRET_OPENCORPORATES_API_TOKEN`). It is never a parameter, and is masked in every URL and error. A test asserts
  that it never reaches logs, errors, runs or artifacts.
- **Budgeted.** At most `max_requests` per run, rate-limited. The run **stops at the first 429/403 quota response**,
  keeps what it got and records `quota_hit`.
- **Targeted, not bulk.** It searches the names Gold already has (multi-location organizations, then Wikidata
  companies). Names searched within `refresh_days` are skipped.
- Data is ODbL: attribution and share-alike apply to anything you publish from it.

Without a token, the run fails at DISCOVER with instructions, and nothing is requested. The Sources page shows
"needs credentials".

## Incremental processing (designed, not yet implemented for OSM)

`capabilities.supports_incremental` exists, and Validate already stores the replication state
(`dp.pipeline_checkpoints`: `osm:<country>` / `replication`). The intended path is:

```
apply OSM replication diffs (.osc) from the stored sequence
  → changed Bronze rows (new Bronze version containing only the changes, or a delta partition)
  → re-normalize the affected Silver records
  → re-resolve only matches touching changed records
  → rebuild the affected Gold entities
```

Today the unit of incrementality is the country partition: a new extract rebuilds only that country's Silver and
Gold, and stages whose inputs did not change are skipped.
