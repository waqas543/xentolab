# Operations

## Local setup

The prerequisites are PostgreSQL 15+ with PostGIS, Python 3.12+, and Node via nvm (`frontend/.nvmrc`).

```bash
make setup                      # venv deps, migrations, sync adapters, npm install
make up                         # API :8000 + worker + web :3000 in one terminal (Ctrl+C stops all)
make pipeline-osm-pakistan      # queue + execute OSM Pakistan in the foreground (PBF=path to use a local file)
make test                       # backend tests (uses database ds_platform_test)
make help                       # everything else (individual services, extra workers, other countries)
```

`make up` runs `scripts/dev.sh`. It loads `.env` if present, and checks the virtualenv, `node_modules`, PostgreSQL and
free ports. It applies migrations and syncs sources, then supervises the three processes: logs are prefixed per
service, Ctrl+C stops all of them including child processes, and if any one exits the rest are stopped too. Options:
`API_PORT`, `WEB_PORT`, `WORKERS`, `WORKER_RELOAD` (default 1: workers restart when backend code changes; a run in
progress is cancelled and can be retried, with finished stages skipped).

## Configuration (environment)

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | `dbname=ds_companies` | default DSN for both planes |
| `CONTROL_DATABASE_URL` / `SERVING_DATABASE_URL` | `DATABASE_URL` | split control plane and serving data |
| `DATA_PLATFORM_DATA_DIR` | `./data` | artifact and lake root |
| `DATA_PLATFORM_AUTH_MODE` | `dev` | `dev` (all permissions, local only) or `api_key` |
| `DATA_PLATFORM_MIN_FREE_DISK_MB` | `500` | disk reserve kept free |
| `DATA_PLATFORM_HEARTBEAT_SECONDS` | `10` | worker heartbeat and cancellation-watchdog interval |
| `DATA_PLATFORM_STALE_RUN_SECONDS` | `120` | when a silent worker's run is failed |
| `DATA_PLATFORM_WORKER_POLL_SECONDS` | `2` | idle poll interval |
| `DATA_PLATFORM_EXTRA_SOURCES` | – | extra adapters, `pkg.module:Class,…` |
| `DP_SECRET_<NAME>` | – | provider credentials, read by adapters as `settings.secret("<NAME>")`; e.g. `DP_SECRET_OPENCORPORATES_API_TOKEN` |
| `DATA_PLATFORM_USER_AGENT` | `osm-companies-data-platform/1.0 (local)` | sent to every provider; Wikimedia asks for contact details here |
| `GEONAMES_BASE_URL`, `WIKIDATA_SPARQL_URL`, `WIKIDATA_API_URL`, `COMMONCRAWL_DATA_URL`, `GDELT_BASE_URL`, `OPENCORPORATES_API_URL`, `OVERTURE_STAC_URL` | official endpoints | mirrors or local fixtures (the tests point them at a local server) |

`make up` loads `.env`, so this is the place for `DP_SECRET_*` values. Restart `make up` after changing it: the
Sources page shows whether each needed credential is configured, but never its value.

Secrets never appear in parameters (rejected at validation), logs, error records or API responses. Log records, error
messages, tracebacks and parameters pass through `observability.redact`.

## Permissions

`data_platform.view`, `.run_pipeline`, `.cancel_pipeline`, `.configure_source`, `.view_raw_data` (Bronze datasets),
`.export_data`, `.review_matches`. In `api_key` mode:

```bash
python -m data_platform api-key analyst --permission data_platform.view --permission data_platform.export_data
```

Keys are stored as SHA-256 hashes and shown once. Note that the v1 SQL query runner (`/api/query`) is not covered by
these permissions; keep it local or put it behind your own auth.

## Observability

- **Structured logs.** JSON on stdout with `pipeline_run_id`, `stage`, `stage_run_id`, `source`, `country`, `dataset`
  and `worker` on every line. INFO and above inside a run is also persisted to `dp.pipeline_logs` and shown in the UI.
- **Metrics.** `GET /api/data-platform/metrics` (Prometheus text, computed from the control plane, so it is correct
  across workers): `pipeline_runs_total`, `pipeline_failures_total`, `records_processed_total`,
  `records_rejected_total`, `stage_duration_seconds`, `stage_retries_total`, `bytes_downloaded_total`,
  `processing_rate`. Per-run metrics are in `dp.pipeline_metrics`.

## Running every source for a country

```bash
make pipeline-all COUNTRY=PK          # queue GeoNames, OSM, Overture, Wikidata, Common Crawl, GDELT in order
make pipeline-gdelt COUNTRY=PK        # later: only the news since the previous run (incremental)
make pipeline-wikidata COUNTRY=PK     # later: only edited entities are fetched again
```

GeoNames goes first because it changes the shared geography. A source whose Silver was built before the gazetteer
existed rebuilds its Silver on its next run. You can also backfill it with `-p start_stage=SILVER_TRANSFORM`.

Measured on Pakistan (October 2026; this Mac's Python runs under Rosetta):

| Source | Network per run | Run time | Result |
|---|---|---|---|
| GeoNames | 11 MB (the server is slow) | 5.5 min, of which 4.5 min download | 230k features → 150k populated places in the gazetteer, 2,563 postal codes, 1,919 facilities in `silver.places` |
| Common Crawl (CC-MAIN-2026-39) | ~900 index reads + 806 blocks (~210 MB) | 6 min | 2.13M captures → 26,947 domains, 21,233 of them unknown to any business (15,315 business candidates) |
| GDELT, 6-hour window | ~0.4 GB streamed (192 files per day) | 2.5 min | 635 records about PK, 1,125 organizations |
| Wikidata, first snapshot | 1 listing query + ~100 revision lookups + ~775 entity requests | 1.5–2.5 h at 2 parallel requests (the API answers in ~5–10 s per 50 entities) | ~38.6k entities. Later runs fetch only edited entities. |
| OpenCorporates | ≤ `max_requests` API calls | – | needs your token |

What stays on disk: the GeoNames bundle and Parquet, the Common Crawl blocks byte-for-byte (~210 MB) plus Bronze
(~145 MB), only the matching GDELT records (a few MB), and the Wikidata snapshot (gzip JSON lines).

**Keep the machine awake for long runs.** Runs pause while a laptop sleeps and resume on wake: heartbeats resume, and
in-flight requests time out and are retried. A first Wikidata snapshot is the longest run. Close the lid and it simply
takes longer.

## Runbook

| Situation | Action |
|---|---|
| A run is stuck in a stage | Cancel it (UI or `python -m data_platform cancel <id>`). The watchdog interrupts DuckDB queries. If the worker process died, the run is failed automatically after the stale timeout. |
| A run failed | Read the Errors tab (type, attempt, context, traceback), fix the cause, then **Retry**. Finished stages are skipped. |
| Data looks wrong after a code change | Bump `transformation_version`, or run a backfill with `start_stage=SILVER_TRANSFORM` and `rebuild_silver=true`. Old versions stay for comparison. |
| A bad merge in Gold | Reject the match in the Explorer, then run `start_stage=ENTITY_RESOLUTION, rebuild_gold=true`. |
| Disk filling up | `InsufficientDiskError` stops runs before damage. Old artifacts and non-current versions can be deleted explicitly (`status = 'deleted'` and remove the directory), never while referenced by current versions. |
| Roll back a dataset | Set `is_current` on the earlier committed version (same partition) in one transaction, then run ANALYTICS_REFRESH to republish. |

## Known limitations

- **Python under Rosetta.** The repository's `.venv` uses an x86_64 Python running under Rosetta on Apple Silicon. An
  arm64 Python would speed up Bronze parsing, which is Python-callback bound at about 70k objects/s.
- **DuckDB streaming stall.** DuckDB 1.5.x can stall when streaming certain results through the Arrow reader.
  `silver.py` materializes into a temp table first. The cancellation watchdog also covers this class of hang.
- **No OSM incremental diffs yet.** Replication state is recorded; see [source-adapters.md](source-adapters.md).
- **Common Crawl page content** (fetching homepage WARC records for titles and contacts) is not built yet. Silver
  stores each domain's homepage WARC reference, so it can be added as a separate pipeline.
- **OpenCorporates coverage.** Pakistan's registry (SECP) is not openly available, so registry matches for PK depend
  on what OpenCorporates holds for the searched names.
