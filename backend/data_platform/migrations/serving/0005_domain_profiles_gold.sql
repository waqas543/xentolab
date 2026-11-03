-- gold.domain_profiles becomes a regular Gold serving table: built by GOLD_BUILD from silver.domain_facts
-- (any source) and organization records, published per country like gold.domains. 0004's version was written
-- directly by the scraping engine and holds nothing that the next Gold build does not recreate.
DROP TABLE IF EXISTS gold.domain_profiles;

CREATE TABLE gold.domain_profiles (
    domain              TEXT NOT NULL,
    country_code        TEXT NOT NULL,
    organization_id     TEXT,
    company_name        TEXT,
    summary             TEXT,
    industry            TEXT,
    category_group      TEXT,
    city                TEXT,
    founded_year        INTEGER,
    employees           TEXT,
    market_cap          TEXT,
    revenue             TEXT,
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
    sources             TEXT[] NOT NULL,
    pages_crawled       INTEGER NOT NULL DEFAULT 0,
    fact_count          INTEGER NOT NULL DEFAULT 0,
    field_sources       JSONB NOT NULL DEFAULT '{}',
    observed_at         TIMESTAMPTZ,
    gold_version_id     BIGINT NOT NULL,
    PRIMARY KEY (domain, country_code)
);
CREATE INDEX domain_profiles_category_idx ON gold.domain_profiles (country_code, category_group);
CREATE INDEX domain_profiles_city_idx ON gold.domain_profiles (country_code, city);
CREATE INDEX domain_profiles_organization_idx ON gold.domain_profiles (organization_id) WHERE organization_id IS NOT NULL;
