# Pipeline lifecycle

A **pipeline definition** (e.g. `osm.extract`) is declared by an adapter and synced into `dp.pipeline_definitions`.
A **run** is one execution of it with validated parameters. A run goes through a fixed sequence of **stages**.
Each stage has its own status, attempts, timings, record counts, progress, metrics and errors (`dp.pipeline_stage_runs`).

```mermaid
flowchart LR
  D[DISCOVER] --> DL[DOWNLOAD] --> V[VALIDATE] --> BI[BRONZE_INGEST] --> BQ[BRONZE_QUALITY]
  BQ --> ST[SILVER_TRANSFORM] --> SQ[SILVER_QUALITY] --> ER[ENTITY_RESOLUTION]
  ER --> GB[GOLD_BUILD] --> GQ[GOLD_QUALITY] --> AR[ANALYTICS_REFRESH]
```

| Stage | Implemented by | Writes |
|---|---|---|
| DISCOVER | `adapter.discover_available_datasets` | `run.outputs.discovered` |
| DOWNLOAD | `adapter.download` | `dp.dataset_artifacts` and the artifact file |
| VALIDATE | `adapter.validate_download` | artifact `source_version`, checkpoints |
| BRONZE_INGEST | `adapter.ingest_bronze` | Bronze dataset versions |
| BRONZE/SILVER/GOLD_QUALITY | platform (`quality/`) with checks from the adapter or `quality/suites.py` | `dp.quality_results` |
| SILVER_TRANSFORM | `adapter.transform_silver` | `silver.places`, `silver.rejects`, `silver.geo_boundaries` |
| ENTITY_RESOLUTION | platform (`resolution/`) | `er.entity_matches`, checkpoint `er:<country>` |
| GOLD_BUILD | platform (`gold/builder.py`), plus the optional `adapter.build_gold` | Gold dataset versions |
| ANALYTICS_REFRESH | platform (`gold/publish.py`) and `adapter.collect_metrics` | `gold.*` serving tables, `dp.serving_state`, metrics |

## State machine

All statuses live in `data_platform/states.py`. Transitions are enforced in Python (`check_run_transition`) and by
guarded SQL (`UPDATE … WHERE status = ANY(allowed)`). This holds even when workers and API processes race.

```mermaid
stateDiagram-v2
  [*] --> PENDING
  PENDING --> QUEUED: create_run (same transaction)
  QUEUED --> DISCOVERING: worker claims
  QUEUED --> CANCELLED
  DISCOVERING --> DOWNLOADING --> VALIDATING --> BRONZE_LOADING --> SILVER_PROCESSING
  SILVER_PROCESSING --> ENTITY_RESOLUTION --> GOLD_PROCESSING
  GOLD_PROCESSING --> COMPLETED
  GOLD_PROCESSING --> PARTIALLY_COMPLETED: optional stage failed
  DISCOVERING --> FAILED
  GOLD_PROCESSING --> FAILED
  DOWNLOADING --> CANCELLED
  COMPLETED --> [*]
```

Executing statuses may jump forward, because stages can be skipped. They never go backwards. Terminal statuses are
final, and a retry is always a **new run** linked by `retry_of_run_id`. Stage statuses are `PENDING`, `RUNNING`,
`RETRYING`, `SUCCEEDED`, `SKIPPED`, `FAILED` and `CANCELLED`.

`PARTIALLY_COMPLETED` means every required stage succeeded but an optional one failed. Currently the only optional
stage is ANALYTICS_REFRESH: Gold was built but publishing or metrics failed.

## Queue and workers

- **No dual-write.** `POST /pipelines/{id}/runs` validates parameters and inserts the run and its stage rows. The run
  is created as `QUEUED` in the same transaction, and the endpoint returns 202. Nothing heavy runs in the request.
- **Claiming.** Workers (`python -m data_platform worker`) claim the oldest queued run with
  `SELECT … FOR UPDATE SKIP LOCKED`.
- **Concurrency.** A partial unique index guarantees at most one *executing* run per **concurrency key** (`osm:PK`). Two
  runs that would write the same partitions never overlap; the second waits in the queue.
- **Heartbeats.** Workers heartbeat every 10 s. Runs whose heartbeat is older than `DATA_PLATFORM_STALE_RUN_SECONDS`
  (default 120) are marked `FAILED` with a `WorkerLost` error, and can be retried.
- **Scheduler.** Each worker also runs the scheduler (`engine/scheduler.py`). An advisory lock makes exactly one worker
  the scheduler leader at a time.

## Cross-source stages and the Gold scope lock

ENTITY_RESOLUTION, GOLD_BUILD, GOLD_QUALITY and ANALYTICS_REFRESH combine **every** source's Silver for a country.
They run under a per-country PostgreSQL advisory lock (`engine/locks.py`, key `gold:<country>`), so an OSM run and an
Overture run for the same country never interleave there. Their download, Bronze and Silver stages still run in
parallel. A stage waiting for the lock reports "waiting for another run…" as progress and can be cancelled. If Gold
finds that matches are stale because another source's Silver changed meanwhile, it re-runs resolution first instead
of failing.

Workers also record the code versions they actually executed with on the run (`versions`). If a long-running worker
started before a code change, the lineage shows the truth. Restart workers after changing adapter code.

## Retries

The retry policy is per stage (`StagePolicy`). The decision is made from the **error class**, never from message text:

| Error | Retried? |
|---|---|
| `TransientError`: network, 5xx, 429, timeouts, checksum mismatch after download | yes, with exponential backoff, full jitter, bounded attempts |
| `PermanentError` and subclasses: `ValidationError`, `ParameterError`, `SchemaError`, `QualityGateError`, `InsufficientDiskError` | never |
| unknown exceptions | only if their type looks network-related (`errors.is_retryable`) |

The defaults are: DISCOVER up to 4 attempts, DOWNLOAD up to 5 (it resumes the partial file), other stages 1. Every
attempt's error is stored in `dp.pipeline_errors` with exception type, message, stage, attempt, structured context
and traceback. Secrets are redacted.

## Cancellation (cooperative)

`POST /runs/{id}/cancel` behaves differently depending on where the run is:

- **Queued run:** cancelled immediately.
- **Executing run:** `cancel_requested_at` is set. The running stage notices at its next `ctx.progress()` or
  `ctx.check_cancelled()` call, at most every 2 s.
- **Stage blocked inside a long DuckDB query:** the heartbeat thread sees the flag and calls DuckDB's thread-safe
  `interrupt()` on that run's connections.

In all cases, cleanup callbacks remove staging directories and remaining stages become `CANCELLED`. A worker that
receives SIGTERM requests cancellation of its current run the same way.

## Idempotency

Every stage decides to skip from **persisted facts**, so a retried or resumed run makes the same decision:

| Stage | Skips when |
|---|---|
| DOWNLOAD | an available artifact has the same SHA-256 (local) or the same provider checksum (remote) |
| BRONZE_INGEST | current Bronze versions exist for that artifact with the same `parser_version` |
| *_QUALITY | results already exist for exactly these dataset versions |
| SILVER_TRANSFORM | the current Silver version's inputs are these Bronze versions and its `transformation_version` matches |
| ENTITY_RESOLUTION | checkpoint `er:<country>` records the same Silver versions and algorithm |
| GOLD_BUILD | the current Gold inputs equal the current Silver versions and the ER fingerprint (match decisions) |
| ANALYTICS_REFRESH | `dp.serving_state` already points at the current Gold version |

The `rebuild_bronze`, `rebuild_silver` and `rebuild_gold` parameters force a stage to run anyway. The `force_download`
parameter forces a network fetch, but identical content is still stored only once.

## Backfills

Two parameters control backfills:

- `start_stage` (`BRONZE_INGEST`, `SILVER_TRANSFORM`, `ENTITY_RESOLUTION` or `GOLD_BUILD`) marks earlier stages
  `SKIPPED` with a "Backfill" reason.
- `artifact_id` picks the stored artifact to rebuild from.

For example:

```bash
# Rebuild Silver from artifact 1 without downloading or re-parsing the PBF
python -m data_platform run osm.extract -p country=PK -p artifact_id=1 -p start_stage=SILVER_TRANSFORM -p rebuild_silver=true --wait
# Re-run entity resolution and Gold (e.g. after changing the algorithm or reviewing matches)
python -m data_platform run osm.extract -p country=PK -p start_stage=ENTITY_RESOLUTION -p rebuild_gold=true --wait
```

## Progress

Stages call `ctx.progress(current, total, unit, message)`, which is throttled to one database write per second. The
`total` is passed **only when genuinely known**: download bytes and hashing have one; Bronze parsing does not. The UI
shows a percentage only when the total is set, and otherwise shows processed count, rate and elapsed time. The rate
is a smoothed records-per-second (or bytes-per-second) figure stored in `metrics.rate_per_sec`.

## Scheduling

`dp.pipeline_schedules` supports these cadences: `manual`, `daily`, `weekly`, `monthly` (preset cron), `cron`
(custom), and `on_release`. With `on_release`, the scheduler calls `adapter.latest_release()` every
`check_interval_minutes` and queues a run only when a new provider version appears. Schedules hold their own
parameters; nothing is hard-coded in workers. A schedule never queues a run while another run for the same key is
queued or executing.
