-- Website enrichment: crawl state for the scraping engine (frontier), parsed pages and extracted facts with
-- provenance, and one Gold profile per domain. Raw HTML never lands here; it lives on disk until parsed.
CREATE SCHEMA IF NOT EXISTS web;

CREATE TABLE IF NOT EXISTS web.crawl_campaigns (
    id                  BIGSERIAL PRIMARY KEY,
    status              TEXT NOT NULL DEFAULT 'running',     -- running | completed | cancelled
    country_code        TEXT,
    params              JSONB NOT NULL DEFAULT '{}',
    domain_count        INTEGER NOT NULL DEFAULT 0,
    pipeline_run_id     BIGINT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at         TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS web.crawl_domains (
    campaign_id         BIGINT NOT NULL REFERENCES web.crawl_campaigns (id) ON DELETE CASCADE,
    domain              TEXT NOT NULL,
    country_code        TEXT NOT NULL,
    state               TEXT NOT NULL DEFAULT 'crawling',    -- crawling | enriching | done
    enrich_queued_at    TIMESTAMPTZ,
    finished_at         TIMESTAMPTZ,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (campaign_id, domain)
);
CREATE INDEX IF NOT EXISTS crawl_domains_state_idx ON web.crawl_domains (campaign_id, state);

CREATE TABLE IF NOT EXISTS web.crawl_urls (
    campaign_id         BIGINT NOT NULL REFERENCES web.crawl_campaigns (id) ON DELETE CASCADE,
    url                 TEXT NOT NULL,
    domain              TEXT NOT NULL,
    kind                TEXT NOT NULL,                       -- home | about | contact | team | company
    depth               SMALLINT NOT NULL DEFAULT 0,
    priority            SMALLINT NOT NULL DEFAULT 0,         -- lower is fetched first (from the parser)
    from_url            TEXT,
    state               TEXT NOT NULL,                       -- discovered | queued | written | failed | skipped
    attempts            SMALLINT NOT NULL DEFAULT 0,
    http_status         INTEGER,
    final_url           TEXT,
    error_class         TEXT,
    error_message       TEXT,
    queued_at           TIMESTAMPTZ,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (campaign_id, url)
);
CREATE INDEX IF NOT EXISTS crawl_urls_state_idx ON web.crawl_urls (campaign_id, state);
CREATE INDEX IF NOT EXISTS crawl_urls_domain_idx ON web.crawl_urls (campaign_id, domain);

-- Latest parsed version of each page (clean text only; the HTML is deleted once this row is written).
CREATE TABLE IF NOT EXISTS web.pages (
    domain              TEXT NOT NULL,
    url                 TEXT NOT NULL,
    campaign_id         BIGINT NOT NULL,
    kind                TEXT NOT NULL,
    final_url           TEXT,
    http_status         INTEGER,
    title               TEXT,
    lang                TEXT,
    text                TEXT,
    content_sha256      TEXT,
    bytes               INTEGER,
    parser_version      TEXT NOT NULL,
    fetched_at          TIMESTAMPTZ NOT NULL,
    written_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (domain, url)
);

-- One row per extracted fact. `url` is the evidence page (NULL for domain-level facts such as LLM output).
CREATE TABLE IF NOT EXISTS web.facts (
    id                  BIGSERIAL PRIMARY KEY,
    domain              TEXT NOT NULL,
    url                 TEXT,
    field               TEXT NOT NULL,
    value               TEXT NOT NULL,
    source              TEXT NOT NULL,                       -- jsonld | meta | link | text | llm
    evidence            TEXT,
    confidence          REAL,
    extractor_version   TEXT NOT NULL,
    campaign_id         BIGINT NOT NULL,
    extracted_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS facts_domain_idx ON web.facts (domain, field);
CREATE INDEX IF NOT EXISTS facts_url_idx ON web.facts (domain, url);

CREATE TABLE IF NOT EXISTS gold.domain_profiles (
    domain              TEXT PRIMARY KEY,
    country_code        TEXT NOT NULL,
    company_name        TEXT,
    summary             TEXT,
    industry            TEXT,
    category_group      TEXT,
    city                TEXT,
    founded_year        INTEGER,
    employees           TEXT,
    market_cap          TEXT,
    financial_info      TEXT,
    facebook            TEXT,
    instagram           TEXT,
    x_twitter           TEXT,
    linkedin            TEXT,
    youtube             TEXT,
    tiktok              TEXT,
    pinterest           TEXT,
    whatsapp            TEXT,
    threads             TEXT,
    emails              TEXT[],
    phones              TEXT[],
    wikidata_id         TEXT,
    pages_crawled       INTEGER NOT NULL DEFAULT 0,
    field_sources       JSONB NOT NULL DEFAULT '{}',
    campaign_id         BIGINT,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS domain_profiles_category_idx ON gold.domain_profiles (country_code, category_group);
