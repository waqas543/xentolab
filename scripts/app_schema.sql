-- The `app` schema: everything the mobile app serves, and nothing about where it came from.
--
-- Built from the gold.* serving tables with explicit column lists: source names, per-field provenance,
-- match confidence, lineage, Wikidata / gazetteer ids and crawl statistics are left behind. This schema is
-- what gets dumped and restored into production (`make app-dump`).
--
-- Run in one transaction (psql --single-transaction -v ON_ERROR_STOP=1). It builds `app_build` and swaps it
-- in at the end, so readers of `app` never see a half-built schema.

SET client_min_messages = warning;
DROP SCHEMA IF EXISTS app_build CASCADE;
CREATE SCHEMA app_build;

-- Helpers live in pg_temp so they are not part of the dumped schema.
CREATE FUNCTION pg_temp.category_label(key text) RETURNS text LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
        WHEN key IS NULL OR key = '' THEN NULL
        WHEN key = 'unspecified_shop' THEN 'Shop'
        WHEN key = 'atm' THEN 'ATM'
        WHEN key = 'fuel' THEN 'Fuel station'
        WHEN key = 'college_university' THEN 'College / university'
        WHEN key = 'doctors' THEN 'Doctor'
        WHEN key = 'it' THEN 'IT services'
        WHEN key = 'ngo' THEN 'NGO'
        WHEN key = 'cng' THEN 'CNG station'
        ELSE (SELECT nullif(upper(left(t, 1)) || substr(t, 2), '')
              FROM (SELECT btrim(replace(regexp_replace(split_part(key, ';', 1), '^b2b_', ''), '_', ' ')) AS t) s)
    END
$$;

-- Links to the data providers themselves (a place whose "website" is its Wikidata page, say) are dropped.
CREATE FUNCTION pg_temp.is_source_link(site text) RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
    SELECT coalesce(site ~* '(^|[/.@])(wikidata\.org|openstreetmap\.org|osm\.org|overturemaps\.org|geonames\.org|commoncrawl\.org|gdeltproject\.org|opencorporates\.com)([/:?#]|$)', false)
$$;

CREATE FUNCTION pg_temp.url(site text) RETURNS text LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE WHEN site IS NULL OR site = '' OR pg_temp.is_source_link(site) THEN NULL
                WHEN site ~* '^https?://' THEN site
                ELSE 'https://' || site END
$$;

CREATE FUNCTION pg_temp.city_id(source_id text) RETURNS text LANGUAGE sql IMMUTABLE AS $$
    SELECT 'c' || left(md5(source_id), 15)          -- opaque: never expose gazetteer ids
$$;

-- ---------------------------------------------------------------- cities

CREATE TABLE app_build.cities AS
SELECT pg_temp.city_id(c.city_id)                     AS id,
       c.country_code,
       c.name,
       nullif(c.name_local, c.name)                   AS local_name,
       initcap(coalesce(c.kind, 'place'))             AS kind,
       c.admin1                                       AS province,
       c.admin2                                       AS district,
       c.latitude                                     AS lat,
       c.longitude                                    AS lng,
       c.population,
       c.business_count                               AS places,
       c.with_phone,
       c.with_website,
       c.organizations                                AS companies,
       c.businesses_per_10k                           AS per_10k_people,
       c.top_category_group                           AS top_group
FROM gold.cities c
WHERE c.business_count > 0;

ALTER TABLE app_build.cities ADD PRIMARY KEY (id);
CREATE INDEX ON app_build.cities (country_code, places DESC);
CREATE INDEX ON app_build.cities (name);
CREATE INDEX ON app_build.cities USING gin (name gin_trgm_ops);

-- ---------------------------------------------------------------- places

CREATE TABLE app_build.places AS
WITH city_ids AS (                                   -- the city row that holds each name's counts
    SELECT DISTINCT ON (country_code, name) country_code, name, id
    FROM app_build.cities ORDER BY country_code, name, places DESC
)
SELECT b.gold_id                                      AS id,
       b.country_code,
       b.name,
       coalesce(pg_temp.category_label(b.category), b.category_group) AS category,
       nullif(b.category, '')                         AS category_key,
       b.category_group                               AS group_name,
       nullif(b.admin1, '')                           AS province,
       b.admin2                                       AS district,
       b.city,
       ci.id                                          AS city_id,
       CASE WHEN b.locality <> b.city THEN b.locality END AS area,
       b.postal_code,
       nullif(b.address, '')                          AS address,
       b.latitude                                     AS lat,
       b.longitude                                    AS lng,
       b.geom,
       nullif(b.phone, '')                            AS phone,
       pg_temp.url(b.website)                         AS website,
       nullif(b.email, '')                            AS email,
       CASE WHEN NOT pg_temp.is_source_link(b.domain) THEN b.domain END AS domain,
       nullif(b.brand_name, '')                       AS brand,
       b.organization_id                              AS company_id,
       b.has_phone,
       b.has_website AND pg_temp.url(b.website) IS NOT NULL AS has_website,
       b.has_email,
       b.completeness::int                            AS completeness
FROM gold.businesses b
LEFT JOIN city_ids ci ON ci.country_code = b.country_code AND ci.name = b.city;

ALTER TABLE app_build.places ADD PRIMARY KEY (id);
CREATE INDEX ON app_build.places USING gist (geom);
CREATE INDEX ON app_build.places USING gin (name gin_trgm_ops);
CREATE INDEX ON app_build.places (country_code, group_name, category_key);
CREATE INDEX ON app_build.places (city, area);
CREATE INDEX ON app_build.places (province);
CREATE INDEX ON app_build.places (company_id) WHERE company_id IS NOT NULL;
CREATE INDEX ON app_build.places (domain) WHERE domain IS NOT NULL;
CREATE INDEX ON app_build.places (completeness DESC, name);

-- ---------------------------------------------------------------- companies

CREATE TABLE app_build.companies AS
SELECT o.organization_id                              AS id,
       o.country_code,
       o.name,
       nullif(o.legal_name, o.name)                   AS legal_name,
       upper(left(o.industry, 1)) || substr(o.industry, 2) AS industry,
       o.category_group                               AS group_name,
       o.location_count                               AS locations,
       o.city_count                                   AS cities,
       o.headquarters,
       pg_temp.url(coalesce(nullif(o.website, ''), o.domain)) AS website,
       o.description,
       substring(o.inception FROM '^-?[0-9]{3,4}')    AS founded,
       o.employees,
       o.parent_name                                  AS parent,
       coalesce((SELECT array_agg(a) FROM (SELECT a FROM unnest(o.aliases) a WHERE a <> o.name LIMIT 6) x),
                '{}')                                 AS aliases,
       o.registry_id                                  AS registration_number,
       o.registry_status                              AS status,
       o.news_mentions,
       o.news_last_seen                               AS last_in_news
FROM gold.organizations o;

ALTER TABLE app_build.companies ADD PRIMARY KEY (id, country_code);
CREATE INDEX ON app_build.companies USING gin (name gin_trgm_ops);
CREATE INDEX ON app_build.companies (country_code, locations DESC);

-- ---------------------------------------------------------------- websites

CREATE TABLE app_build.websites AS
SELECT d.domain,
       d.country_code,
       coalesce(CASE WHEN d.homepage_status BETWEEN 200 AND 399            -- the site's root, not the fetched page
                     THEN substring(d.homepage_url FROM '^(https?://[^/]+)') || '/' END,
                'https://' || d.domain)               AS url,
       t.type,
       t.type_label,
       d.business_count                               AS places,
       d.top_category_group                           AS group_name,
       d.organization_id                              AS company_id,
       -- Popularity ranks by linked places, then web footprint; the footprint itself is not exported.
       row_number() OVER (PARTITION BY d.country_code
                          ORDER BY d.business_count DESC, d.crawl_pages DESC NULLS LAST, d.domain)::int AS popularity
FROM gold.domains d
CROSS JOIN LATERAL (
    SELECT CASE WHEN d.domain_type IN ('commercial', 'known_business', 'business') THEN 'business'
                WHEN d.domain_type = 'education' THEN 'education'
                WHEN d.domain_type = 'government' THEN 'government'
                WHEN d.domain_type = 'non-profit' THEN 'nonprofit'
                WHEN d.domain_type IN ('generic', 'network', 'web') THEN 'general'
                ELSE 'other' END AS type
) k
CROSS JOIN LATERAL (
    SELECT k.type, CASE k.type WHEN 'business' THEN 'Business' WHEN 'education' THEN 'Education'
                               WHEN 'government' THEN 'Government' WHEN 'nonprofit' THEN 'Non-profit'
                               WHEN 'general' THEN 'General' ELSE 'Other' END AS type_label
) t
WHERE NOT pg_temp.is_source_link(d.domain);

ALTER TABLE app_build.websites ADD PRIMARY KEY (domain, country_code);
CREATE INDEX ON app_build.websites (country_code, type, popularity);
CREATE INDEX ON app_build.websites (popularity);
CREATE INDEX ON app_build.websites USING gin (domain gin_trgm_ops);

-- ---------------------------------------------------------------- version

CREATE TABLE app_build.dataset_info AS
SELECT to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24MI"Z"') AS version,
       now()                                                          AS built_at,
       (SELECT max(built_at) FROM gold.businesses)                    AS data_updated_at,
       (SELECT array_agg(DISTINCT country_code ORDER BY country_code) FROM app_build.places) AS countries,
       jsonb_build_object(
           'places', (SELECT count(*) FROM app_build.places),
           'companies', (SELECT count(*) FROM app_build.companies),
           'cities', (SELECT count(*) FROM app_build.cities),
           'websites', (SELECT count(*) FROM app_build.websites))     AS row_counts;

-- ---------------------------------------------------------------- guard

-- Refuse to publish if anything source-shaped slipped in (a new column copied by mistake, a raw id).
DO $$
DECLARE
    bad text;
BEGIN
    SELECT string_agg(table_name || '.' || column_name, ', ') INTO bad
    FROM information_schema.columns
    WHERE table_schema = 'app_build'
      AND (column_name ~ '(source|lineage|confidence|wikidata|crawl|enriched|conflict|silver|gold_|identifier|homepage_status|member_count)');
    IF bad IS NOT NULL THEN
        RAISE EXCEPTION 'app schema would expose provenance columns: %', bad;
    END IF;
    IF EXISTS (SELECT 1 FROM app_build.cities WHERE id LIKE '%:%')
       OR EXISTS (SELECT 1 FROM app_build.places WHERE city_id LIKE '%:%') THEN
        RAISE EXCEPTION 'app schema would expose raw city ids';
    END IF;
    IF EXISTS (SELECT 1 FROM app_build.places WHERE pg_temp.is_source_link(website) OR pg_temp.is_source_link(domain)
                                                 OR pg_temp.is_source_link(email))
       OR EXISTS (SELECT 1 FROM app_build.companies WHERE pg_temp.is_source_link(website))
       OR EXISTS (SELECT 1 FROM app_build.websites WHERE pg_temp.is_source_link(domain) OR pg_temp.is_source_link(url)) THEN
        RAISE EXCEPTION 'app schema would link to a data provider';
    END IF;
    IF (SELECT count(*) FROM app_build.places) = 0 THEN
        RAISE EXCEPTION 'gold.businesses is empty; refusing to publish an empty app schema';
    END IF;
END $$;

ANALYZE app_build.places;
ANALYZE app_build.companies;
ANALYZE app_build.cities;
ANALYZE app_build.websites;

-- ---------------------------------------------------------------- swap

DROP SCHEMA IF EXISTS app CASCADE;
ALTER SCHEMA app_build RENAME TO app;
