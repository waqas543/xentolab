-- Serving (data plane) tables in PostgreSQL: Gold copies for low-latency/spatial queries
-- and entity matches, which carry human decisions.
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE SCHEMA IF NOT EXISTS gold;
CREATE SCHEMA IF NOT EXISTS er;

CREATE TABLE er.entity_matches (
    id                  BIGSERIAL PRIMARY KEY,
    left_id             TEXT NOT NULL,      -- silver_id, e.g. osm:n123 (left_id < right_id)
    right_id            TEXT NOT NULL,
    country_code        TEXT,
    match_type          TEXT NOT NULL,      -- location | organization
    match_score         DOUBLE PRECISION NOT NULL,
    name_score          DOUBLE PRECISION,
    domain_score        DOUBLE PRECISION,
    address_score       DOUBLE PRECISION,
    geo_score           DOUBLE PRECISION,
    phone_score         DOUBLE PRECISION,
    distance_m          DOUBLE PRECISION,
    algorithm_version   TEXT NOT NULL,
    status              TEXT NOT NULL CHECK (status IN ('suggested', 'accepted', 'rejected', 'automatic')),
    evidence            JSONB NOT NULL DEFAULT '{}',
    decided_by          TEXT,
    decided_at          TIMESTAMPTZ,
    pipeline_run_id     BIGINT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (left_id < right_id),
    UNIQUE (left_id, right_id, match_type)
);
CREATE INDEX entity_matches_scope_idx ON er.entity_matches (country_code, status);
CREATE INDEX entity_matches_right_idx ON er.entity_matches (right_id);

CREATE TABLE gold.businesses (
    gold_id             TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    normalized_name     TEXT,
    entity_type         TEXT,
    category_group      TEXT,
    category            TEXT,
    country_code        TEXT,
    admin1              TEXT,
    admin2              TEXT,
    city                TEXT,
    locality            TEXT,
    postal_code         TEXT,
    address             TEXT,
    latitude            DOUBLE PRECISION,
    longitude           DOUBLE PRECISION,
    website             TEXT,
    domain              TEXT,
    tld                 TEXT,
    phone               TEXT,
    email               TEXT,
    brand_name          TEXT,
    brand_wikidata      TEXT,
    organization_id     TEXT,
    sources             TEXT[] NOT NULL,
    source_count        SMALLINT NOT NULL,
    member_count        INTEGER NOT NULL,
    has_website         BOOLEAN NOT NULL,
    has_phone           BOOLEAN NOT NULL,
    has_email           BOOLEAN NOT NULL,
    has_address         BOOLEAN NOT NULL,
    completeness        SMALLINT NOT NULL,
    conflicts           TEXT[] NOT NULL DEFAULT '{}',
    source_updated_at   TIMESTAMPTZ,
    gold_version_id     BIGINT NOT NULL,
    built_at            TIMESTAMPTZ NOT NULL,
    geom                geometry(Point, 4326)
        GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)) STORED
);
CREATE INDEX businesses_geom_idx ON gold.businesses USING gist (geom);
CREATE INDEX businesses_country_idx ON gold.businesses (country_code, admin1, city);
CREATE INDEX businesses_category_idx ON gold.businesses (category_group, category);
CREATE INDEX businesses_domain_idx ON gold.businesses (domain) WHERE domain IS NOT NULL;
CREATE INDEX businesses_org_idx ON gold.businesses (organization_id) WHERE organization_id IS NOT NULL;
CREATE INDEX businesses_sources_idx ON gold.businesses USING gin (sources);
CREATE INDEX businesses_name_trgm_idx ON gold.businesses USING gin (name gin_trgm_ops);

CREATE TABLE gold.business_sources (
    gold_id                 TEXT NOT NULL,
    silver_id               TEXT NOT NULL,
    country_code            TEXT,
    source                  TEXT NOT NULL,
    source_record_id        TEXT NOT NULL,
    artifact_id             BIGINT,
    silver_version_id       BIGINT,
    pipeline_run_id         BIGINT,
    transformation_version  TEXT,
    match_confidence        DOUBLE PRECISION NOT NULL,
    is_primary              BOOLEAN NOT NULL,
    PRIMARY KEY (gold_id, silver_id)
);
CREATE INDEX business_sources_silver_idx ON gold.business_sources (silver_id);
CREATE INDEX business_sources_country_idx ON gold.business_sources (country_code);

CREATE TABLE gold.organizations (
    organization_id     TEXT NOT NULL,
    country_code        TEXT NOT NULL,
    name                TEXT NOT NULL,
    brand_wikidata      TEXT,
    domain              TEXT,
    category_group      TEXT,
    location_count      INTEGER NOT NULL,
    city_count          INTEGER NOT NULL,
    sources             TEXT[] NOT NULL,
    gold_version_id     BIGINT NOT NULL,
    PRIMARY KEY (organization_id, country_code)
);

CREATE TABLE gold.domains (
    domain              TEXT NOT NULL,
    country_code        TEXT NOT NULL,
    tld                 TEXT NOT NULL,
    business_count      INTEGER NOT NULL,
    organization_count  INTEGER NOT NULL,
    sources             TEXT[] NOT NULL,
    top_category_group  TEXT,
    gold_version_id     BIGINT NOT NULL,
    PRIMARY KEY (domain, country_code)
);
CREATE INDEX domains_tld_idx ON gold.domains (tld);
