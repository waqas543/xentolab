-- Gold v3: organizations merge registry / knowledge-base records and news signals; organization provenance;
-- crawled web domains; cities with population. Businesses record which non-location sources enrich them.
ALTER TABLE gold.businesses ADD COLUMN IF NOT EXISTS wikidata_id TEXT;
ALTER TABLE gold.businesses ADD COLUMN IF NOT EXISTS enriched_by TEXT[] NOT NULL DEFAULT '{}';
CREATE INDEX IF NOT EXISTS businesses_enriched_by_idx ON gold.businesses USING gin (enriched_by);

ALTER TABLE gold.organizations
    ADD COLUMN IF NOT EXISTS legal_name TEXT,
    ADD COLUMN IF NOT EXISTS wikidata_id TEXT,
    ADD COLUMN IF NOT EXISTS website TEXT,
    ADD COLUMN IF NOT EXISTS description TEXT,
    ADD COLUMN IF NOT EXISTS industry TEXT,
    ADD COLUMN IF NOT EXISTS headquarters TEXT,
    ADD COLUMN IF NOT EXISTS inception TEXT,
    ADD COLUMN IF NOT EXISTS employees BIGINT,
    ADD COLUMN IF NOT EXISTS parent_name TEXT,
    ADD COLUMN IF NOT EXISTS registry_id TEXT,
    ADD COLUMN IF NOT EXISTS jurisdiction TEXT,
    ADD COLUMN IF NOT EXISTS registry_status TEXT,
    ADD COLUMN IF NOT EXISTS identifiers JSONB NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS aliases TEXT[],
    ADD COLUMN IF NOT EXISTS linked_records INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS news_mentions BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS news_articles BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS news_last_seen TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS news_tone DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS field_sources JSONB NOT NULL DEFAULT '{}';
CREATE INDEX IF NOT EXISTS organizations_name_trgm_idx ON gold.organizations USING gin (name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS organizations_wikidata_idx ON gold.organizations (wikidata_id) WHERE wikidata_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS gold.organization_sources (
    organization_id         TEXT NOT NULL,
    silver_id               TEXT NOT NULL,
    country_code            TEXT NOT NULL,
    source                  TEXT NOT NULL,
    source_record_id        TEXT NOT NULL,
    link_method             TEXT NOT NULL,      -- wikidata_id | domain | name_geo | name | identifier | ... | standalone
    confidence              DOUBLE PRECISION,
    artifact_id             BIGINT,
    pipeline_run_id         BIGINT,
    transformation_version  TEXT,
    source_url              TEXT,
    PRIMARY KEY (organization_id, silver_id)
);
CREATE INDEX IF NOT EXISTS organization_sources_silver_idx ON gold.organization_sources (silver_id);
CREATE INDEX IF NOT EXISTS organization_sources_country_idx ON gold.organization_sources (country_code);

ALTER TABLE gold.domains
    ADD COLUMN IF NOT EXISTS organization_id TEXT,
    ADD COLUMN IF NOT EXISTS domain_type TEXT,
    ADD COLUMN IF NOT EXISTS is_business_candidate BOOLEAN,
    ADD COLUMN IF NOT EXISTS crawl_pages BIGINT,
    ADD COLUMN IF NOT EXISTS crawl_captures BIGINT,
    ADD COLUMN IF NOT EXISTS crawl_hosts INTEGER,
    ADD COLUMN IF NOT EXISTS crawl_first_seen TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS crawl_last_seen TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS crawl_languages TEXT[],
    ADD COLUMN IF NOT EXISTS homepage_url TEXT,
    ADD COLUMN IF NOT EXISTS homepage_status INTEGER,
    ADD COLUMN IF NOT EXISTS crawl TEXT;
CREATE INDEX IF NOT EXISTS domains_type_idx ON gold.domains (country_code, domain_type);

CREATE TABLE IF NOT EXISTS gold.cities (
    city_id             TEXT NOT NULL,
    country_code        TEXT NOT NULL,
    name                TEXT NOT NULL,
    name_local          TEXT,
    kind                TEXT,
    admin1              TEXT,
    admin2              TEXT,
    latitude            DOUBLE PRECISION,
    longitude           DOUBLE PRECISION,
    population          BIGINT,
    business_count      INTEGER NOT NULL,
    with_website        INTEGER NOT NULL,
    with_phone          INTEGER NOT NULL,
    organizations       INTEGER NOT NULL,
    businesses_per_10k  DOUBLE PRECISION,
    top_category_group  TEXT,
    source              TEXT,
    gold_version_id     BIGINT NOT NULL,
    PRIMARY KEY (city_id, country_code)
);
CREATE INDEX IF NOT EXISTS cities_name_idx ON gold.cities (country_code, name);
