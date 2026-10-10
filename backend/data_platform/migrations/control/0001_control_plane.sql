-- Control plane: what runs, when, its state, artifacts, catalog, quality, access.
CREATE SCHEMA IF NOT EXISTS dp;

CREATE TABLE dp.data_sources (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT,
    homepage        TEXT,
    license         TEXT,
    enabled         BOOLEAN NOT NULL DEFAULT true,
    capabilities    JSONB NOT NULL DEFAULT '{}',
    config          JSONB NOT NULL DEFAULT '{}',      -- non-secret settings only
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE dp.pipeline_definitions (
    id                  TEXT PRIMARY KEY,
    source_id           TEXT NOT NULL REFERENCES dp.data_sources(id),
    name                TEXT NOT NULL,
    description         TEXT,
    parameter_schema    JSONB NOT NULL,
    default_parameters  JSONB NOT NULL DEFAULT '{}',
    stages              TEXT[] NOT NULL,
    quality_overrides   JSONB NOT NULL DEFAULT '{}',
    enabled             BOOLEAN NOT NULL DEFAULT true,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE dp.pipeline_schedules (
    id                      BIGSERIAL PRIMARY KEY,
    pipeline_definition_id  TEXT NOT NULL REFERENCES dp.pipeline_definitions(id),
    name                    TEXT NOT NULL,
    parameters              JSONB NOT NULL DEFAULT '{}',
    cadence                 TEXT NOT NULL CHECK (cadence IN ('manual', 'daily', 'weekly', 'monthly', 'cron', 'on_release')),
    cron                    TEXT,
    check_interval_minutes  INTEGER NOT NULL DEFAULT 360,   -- on_release polling interval
    enabled                 BOOLEAN NOT NULL DEFAULT true,
    next_run_at             TIMESTAMPTZ,
    last_enqueued_at        TIMESTAMPTZ,
    last_run_id             BIGINT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE dp.pipeline_runs (
    id                      BIGSERIAL PRIMARY KEY,
    pipeline_definition_id  TEXT NOT NULL REFERENCES dp.pipeline_definitions(id),
    source_id               TEXT NOT NULL REFERENCES dp.data_sources(id),
    status                  TEXT NOT NULL,
    trigger_type            TEXT NOT NULL CHECK (trigger_type IN ('manual', 'schedule', 'retry', 'backfill', 'api', 'cli')),
    triggered_by            TEXT,
    parameters              JSONB NOT NULL DEFAULT '{}',
    concurrency_key         TEXT NOT NULL,
    country                 TEXT,
    dataset                 TEXT,
    retry_of_run_id         BIGINT REFERENCES dp.pipeline_runs(id),
    schedule_id             BIGINT REFERENCES dp.pipeline_schedules(id),
    worker_id               TEXT,
    heartbeat_at            TIMESTAMPTZ,
    cancel_requested_at     TIMESTAMPTZ,
    cancel_requested_by     TEXT,
    current_stage           TEXT,
    records_discovered      BIGINT NOT NULL DEFAULT 0,
    records_downloaded      BIGINT NOT NULL DEFAULT 0,
    bronze_records          BIGINT NOT NULL DEFAULT 0,
    silver_records          BIGINT NOT NULL DEFAULT 0,
    gold_records            BIGINT NOT NULL DEFAULT 0,
    bytes_downloaded        BIGINT NOT NULL DEFAULT 0,
    error_count             INTEGER NOT NULL DEFAULT 0,
    warning_count           INTEGER NOT NULL DEFAULT 0,
    versions                JSONB NOT NULL DEFAULT '{}',   -- parser / transform / ER / gold versions used
    outputs                 JSONB NOT NULL DEFAULT '{}',   -- artifact + dataset versions produced
    error_summary           TEXT,
    queued_at               TIMESTAMPTZ,
    started_at              TIMESTAMPTZ,
    finished_at             TIMESTAMPTZ,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX pipeline_runs_status_idx ON dp.pipeline_runs (status, created_at);
CREATE INDEX pipeline_runs_definition_idx ON dp.pipeline_runs (pipeline_definition_id, created_at DESC);
CREATE INDEX pipeline_runs_source_idx ON dp.pipeline_runs (source_id, created_at DESC);
-- At most one *executing* run per concurrency key (e.g. osm:PK); enforced by the database.
CREATE UNIQUE INDEX pipeline_runs_one_active_idx ON dp.pipeline_runs (concurrency_key)
    WHERE status NOT IN ('PENDING', 'QUEUED', 'COMPLETED', 'FAILED', 'CANCELLED', 'PARTIALLY_COMPLETED');

CREATE TABLE dp.pipeline_stage_runs (
    id                  BIGSERIAL PRIMARY KEY,
    run_id              BIGINT NOT NULL REFERENCES dp.pipeline_runs(id) ON DELETE CASCADE,
    stage               TEXT NOT NULL,
    position            SMALLINT NOT NULL,
    status              TEXT NOT NULL,
    attempt             SMALLINT NOT NULL DEFAULT 0,
    max_attempts        SMALLINT NOT NULL DEFAULT 1,
    started_at          TIMESTAMPTZ,
    finished_at         TIMESTAMPTZ,
    next_retry_at       TIMESTAMPTZ,
    records_in          BIGINT,
    records_out         BIGINT,
    records_rejected    BIGINT,
    bytes_processed     BIGINT,
    progress_current    BIGINT,
    progress_total      BIGINT,
    progress_unit       TEXT,
    progress_message    TEXT,
    progress_updated_at TIMESTAMPTZ,
    metrics             JSONB NOT NULL DEFAULT '{}',
    skip_reason         TEXT,
    error_message       TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, stage)
);

CREATE TABLE dp.pipeline_errors (
    id              BIGSERIAL PRIMARY KEY,
    run_id          BIGINT NOT NULL REFERENCES dp.pipeline_runs(id) ON DELETE CASCADE,
    stage_run_id    BIGINT REFERENCES dp.pipeline_stage_runs(id) ON DELETE CASCADE,
    stage           TEXT,
    attempt         SMALLINT,
    exception_type  TEXT NOT NULL,
    message         TEXT NOT NULL,
    retryable       BOOLEAN NOT NULL,
    context         JSONB NOT NULL DEFAULT '{}',
    traceback       TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX pipeline_errors_run_idx ON dp.pipeline_errors (run_id, id);

CREATE TABLE dp.pipeline_logs (
    id          BIGSERIAL PRIMARY KEY,
    run_id      BIGINT NOT NULL REFERENCES dp.pipeline_runs(id) ON DELETE CASCADE,
    stage       TEXT,
    level       TEXT NOT NULL,
    message     TEXT NOT NULL,
    context     JSONB NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX pipeline_logs_run_idx ON dp.pipeline_logs (run_id, id);

CREATE TABLE dp.pipeline_metrics (
    id          BIGSERIAL PRIMARY KEY,
    run_id      BIGINT NOT NULL REFERENCES dp.pipeline_runs(id) ON DELETE CASCADE,
    stage       TEXT,
    name        TEXT NOT NULL,
    value       DOUBLE PRECISION NOT NULL,
    labels      JSONB NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX pipeline_metrics_name_idx ON dp.pipeline_metrics (name, created_at);
CREATE INDEX pipeline_metrics_run_idx ON dp.pipeline_metrics (run_id);

CREATE TABLE dp.pipeline_checkpoints (
    scope       TEXT NOT NULL,          -- e.g. osm:PK
    key         TEXT NOT NULL,          -- e.g. replication_sequence
    value       JSONB NOT NULL,
    run_id      BIGINT REFERENCES dp.pipeline_runs(id) ON DELETE SET NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (scope, key)
);

CREATE TABLE dp.dataset_artifacts (
    id                      BIGSERIAL PRIMARY KEY,
    source_id               TEXT NOT NULL REFERENCES dp.data_sources(id),
    dataset_name            TEXT NOT NULL,
    country                 TEXT,
    region                  TEXT,
    source_url              TEXT,
    storage_key             TEXT,
    filename                TEXT NOT NULL,
    format                  TEXT NOT NULL,
    compression             TEXT,
    size_bytes              BIGINT,
    sha256                  TEXT,
    source_checksum         TEXT,          -- provider-published checksum (e.g. md5), when available
    source_version          TEXT,
    source_release_date     TIMESTAMPTZ,
    download_started_at     TIMESTAMPTZ,
    download_completed_at   TIMESTAMPTZ,
    status                  TEXT NOT NULL CHECK (status IN ('downloading', 'available', 'invalid', 'failed', 'deleted')),
    metadata                JSONB NOT NULL DEFAULT '{}',
    pipeline_run_id         BIGINT REFERENCES dp.pipeline_runs(id) ON DELETE SET NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Identical content is stored once per source.
CREATE UNIQUE INDEX dataset_artifacts_content_idx ON dp.dataset_artifacts (source_id, sha256) WHERE status = 'available';
CREATE INDEX dataset_artifacts_lookup_idx ON dp.dataset_artifacts (source_id, dataset_name, country, created_at DESC);

CREATE TABLE dp.datasets (
    id                  TEXT PRIMARY KEY,           -- e.g. silver.places
    layer               TEXT NOT NULL CHECK (layer IN ('bronze', 'silver', 'gold')),
    name                TEXT NOT NULL,
    description         TEXT,
    source_id           TEXT REFERENCES dp.data_sources(id),   -- NULL = cross-source
    partition_columns   TEXT[] NOT NULL DEFAULT '{}',
    format              TEXT NOT NULL DEFAULT 'parquet',
    serving_table       TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE dp.dataset_versions (
    id                  BIGSERIAL PRIMARY KEY,
    dataset_id          TEXT NOT NULL REFERENCES dp.datasets(id),
    partition           JSONB NOT NULL DEFAULT '{}',
    partition_key       TEXT NOT NULL,              -- canonical "k=v/k=v" form
    status              TEXT NOT NULL CHECK (status IN ('building', 'committed', 'failed', 'deleted')),
    is_current          BOOLEAN NOT NULL DEFAULT false,
    location            TEXT NOT NULL,              -- storage key of the version directory
    file_count          INTEGER,
    row_count           BIGINT,
    size_bytes          BIGINT,
    schema              JSONB,
    schema_hash         TEXT,
    versions            JSONB NOT NULL DEFAULT '{}',   -- parser / transformation / algorithm versions
    inputs              JSONB NOT NULL DEFAULT '[]',   -- lineage: upstream artifacts / dataset versions
    pipeline_run_id     BIGINT REFERENCES dp.pipeline_runs(id) ON DELETE SET NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    committed_at        TIMESTAMPTZ
);
CREATE UNIQUE INDEX dataset_versions_current_idx ON dp.dataset_versions (dataset_id, partition_key) WHERE is_current;
CREATE INDEX dataset_versions_dataset_idx ON dp.dataset_versions (dataset_id, created_at DESC);
CREATE INDEX dataset_versions_run_idx ON dp.dataset_versions (pipeline_run_id);

CREATE TABLE dp.quality_results (
    id                  BIGSERIAL PRIMARY KEY,
    pipeline_run_id     BIGINT REFERENCES dp.pipeline_runs(id) ON DELETE CASCADE,
    stage_run_id        BIGINT REFERENCES dp.pipeline_stage_runs(id) ON DELETE CASCADE,
    stage               TEXT NOT NULL,
    dataset_id          TEXT NOT NULL,
    dataset_version_id  BIGINT REFERENCES dp.dataset_versions(id) ON DELETE SET NULL,
    check_name          TEXT NOT NULL,
    check_type          TEXT NOT NULL,
    severity            TEXT NOT NULL CHECK (severity IN ('error', 'warn')),
    status              TEXT NOT NULL CHECK (status IN ('passed', 'warned', 'failed', 'error')),
    records_checked     BIGINT,
    records_failed      BIGINT,
    failure_percentage  DOUBLE PRECISION,
    threshold_percentage DOUBLE PRECISION,
    details             JSONB NOT NULL DEFAULT '{}',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX quality_results_run_idx ON dp.quality_results (pipeline_run_id);
CREATE INDEX quality_results_dataset_idx ON dp.quality_results (dataset_id, created_at DESC);

-- Which dataset version is loaded into each PostgreSQL serving table partition.
CREATE TABLE dp.serving_state (
    dataset_id          TEXT NOT NULL,
    partition_key       TEXT NOT NULL,
    dataset_version_id  BIGINT NOT NULL REFERENCES dp.dataset_versions(id),
    row_count           BIGINT,
    pipeline_run_id     BIGINT REFERENCES dp.pipeline_runs(id) ON DELETE SET NULL,
    published_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (dataset_id, partition_key)
);

CREATE TABLE dp.saved_views (
    id          BIGSERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT,
    dataset_id  TEXT NOT NULL,
    query       JSONB NOT NULL,         -- filters / sort / columns / search (validated DSL)
    owner       TEXT NOT NULL,
    shared      BOOLEAN NOT NULL DEFAULT false,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE dp.api_keys (
    id          BIGSERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    key_prefix  TEXT NOT NULL,
    key_hash    TEXT NOT NULL UNIQUE,   -- sha256 of the key; the key itself is never stored
    permissions TEXT[] NOT NULL,
    active      BOOLEAN NOT NULL DEFAULT true,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at TIMESTAMPTZ
);
