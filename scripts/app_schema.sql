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

-- Ratings are ranked by a review-weighted score: a 5.0 from 3 reviews is not better than a 4.6 from 900. The score
-- pulls each rating towards the country's average by RATING_PRIOR_REVIEWS virtual reviews.
CREATE TEMP TABLE rating_prior AS
SELECT country_code, sum(rating * review_count) / nullif(sum(review_count), 0) AS mean, 20 AS weight
FROM gold.businesses WHERE rating IS NOT NULL AND review_count > 0 GROUP BY country_code;

CREATE TABLE app_build.places AS
SELECT b.gold_id                                      AS id,
       b.country_code,
       b.name,
       nullif(b.name_local, b.name)                   AS local_name,
       coalesce(b.category_label, pg_temp.category_label(b.category), b.category_group) AS category,
       nullif(b.category, '')                         AS category_key,
       b.category_group                               AS group_name,
       pg_temp.category_label(b.subcategory)          AS subcategory,
       nullif(b.admin1, '')                           AS province,
       b.admin2                                       AS district,
       b.city,
       pg_temp.city_id(b.city_id)                     AS city_id,
       CASE WHEN b.locality <> b.city THEN b.locality END AS area,
       b.postal_code,
       nullif(b.address, '')                          AS address,
       -- A provider's town-centre default is not a position: such places stay listed in their city but are left
       -- off maps, "nearby" and distances.
       CASE WHEN coalesce(b.location_precision, 'exact') = 'exact' THEN b.latitude END  AS lat,
       CASE WHEN coalesce(b.location_precision, 'exact') = 'exact' THEN b.longitude END AS lng,
       CASE WHEN coalesce(b.location_precision, 'exact') = 'exact' THEN b.geom END      AS geom,
       coalesce(b.location_precision = 'approximate', false) AS approximate_location,
       nullif(b.phone, '')                            AS phone,
       coalesce(b.phone_kind, CASE WHEN b.phone IS NOT NULL THEN 'direct' END) AS phone_type,
       pg_temp.url(b.website)                         AS website,
       b.website_kind = 'shared'                      AS website_shared,
       pg_temp.url(b.social_url)                      AS social,
       nullif(b.email, '')                            AS email,
       CASE WHEN NOT pg_temp.is_source_link(b.domain) THEN b.domain END AS domain,
       nullif(b.brand_name, '')                       AS brand,
       b.organization_id                              AS company_id,
       b.opening_hours,
       b.opening_hours = '24/7'                       AS open_24_hours,
       b.has_phone,
       b.has_website AND pg_temp.url(b.website) IS NOT NULL AS has_website,
       b.has_email,
       b.completeness::int                            AS completeness,
       round(b.rating::numeric, 1)::float8            AS rating,
       b.review_count                                 AS review_count,
       CASE WHEN b.rating IS NOT NULL AND b.review_count > 0
            THEN round(((b.rating * b.review_count + rp.mean * rp.weight) / (b.review_count + rp.weight))::numeric, 3)::float8
       END                                            AS rating_score
FROM gold.businesses b
LEFT JOIN rating_prior rp ON rp.country_code = b.country_code;

ALTER TABLE app_build.places ADD PRIMARY KEY (id);
CREATE INDEX ON app_build.places USING gist (geom);
CREATE INDEX ON app_build.places USING gin (name gin_trgm_ops);
CREATE INDEX ON app_build.places (country_code, group_name, category_key);
CREATE INDEX ON app_build.places (city, area);
CREATE INDEX ON app_build.places (city_id, area);
CREATE INDEX ON app_build.places (province);
CREATE INDEX ON app_build.places (company_id) WHERE company_id IS NOT NULL;
CREATE INDEX ON app_build.places (domain) WHERE domain IS NOT NULL;
CREATE INDEX ON app_build.places (completeness DESC, name);

-- ---------------------------------------------------------------- market statistics
-- Pre-aggregated so insight screens (a category's market, a city's profile, a brand's position) answer instantly.

CREATE TABLE app_build.category_stats AS
SELECT country_code, category_key, min(category) AS label,
       mode() WITHIN GROUP (ORDER BY group_name) AS group_name,
       count(*)::int AS places, count(DISTINCT city_id)::int AS cities,
       count(*) FILTER (WHERE has_phone)::int AS with_phone,
       count(*) FILTER (WHERE has_website)::int AS with_website,
       count(*) FILTER (WHERE social IS NOT NULL)::int AS with_social,
       count(rating_score)::int AS rated, avg(rating_score)::float8 AS avg_rating,
       count(*) FILTER (WHERE open_24_hours)::int AS open_24h,
       count(company_id)::int AS in_chains
FROM app_build.places WHERE category_key IS NOT NULL GROUP BY 1, 2;
ALTER TABLE app_build.category_stats ADD PRIMARY KEY (country_code, category_key);

CREATE TABLE app_build.city_category_stats AS
SELECT city_id, category_key, count(*)::int AS places,
       count(*) FILTER (WHERE has_phone)::int AS with_phone,
       count(*) FILTER (WHERE has_website)::int AS with_website,
       count(rating_score)::int AS rated, avg(rating_score)::float8 AS avg_rating,
       count(*) FILTER (WHERE open_24_hours)::int AS open_24h
FROM app_build.places WHERE city_id IS NOT NULL AND category_key IS NOT NULL GROUP BY 1, 2;
ALTER TABLE app_build.city_category_stats ADD PRIMARY KEY (city_id, category_key);
CREATE INDEX ON app_build.city_category_stats (category_key, places DESC);

CREATE TABLE app_build.country_stats AS
SELECT country_code, count(*)::int AS places,
       count(*) FILTER (WHERE has_phone)::int AS with_phone,
       count(*) FILTER (WHERE has_website)::int AS with_website,
       count(*) FILTER (WHERE social IS NOT NULL)::int AS with_social,
       count(rating_score)::int AS rated, avg(rating_score)::float8 AS avg_rating,
       count(*) FILTER (WHERE open_24_hours)::int AS open_24h
FROM app_build.places GROUP BY 1;
ALTER TABLE app_build.country_stats ADD PRIMARY KEY (country_code);

CREATE INDEX ON app_build.places (category_key, city_id);
CREATE INDEX ON app_build.places (category_key, rating_score DESC NULLS LAST) WHERE rating_score IS NOT NULL;

-- ---------------------------------------------------------------- companies

CREATE TABLE app_build.companies AS
SELECT o.organization_id                              AS id,
       o.country_code,
       o.name,
       nullif(o.legal_name, o.name)                   AS legal_name,
       nullif(upper(left(o.industry, 1)) || substr(o.industry, 2), '') AS industry,
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
       coalesce(p.category_group, d.top_category_group) AS group_name,
       d.organization_id                              AS company_id,
       -- The company profile read from the site and other sources (gold.domain_profiles), without provenance.
       p.company_name                                 AS name,
       p.summary,
       upper(left(p.industry, 1)) || substr(p.industry, 2) AS industry,
       p.city,
       p.founded_year                                 AS founded,
       p.employees,
       p.market_cap,
       p.revenue,
       p.financial_info                               AS financials,
       coalesce(jsonb_strip_nulls(jsonb_build_object(
           'facebook', p.facebook, 'instagram', p.instagram, 'x', p.x_twitter, 'linkedin', p.linkedin,
           'youtube', p.youtube, 'tiktok', p.tiktok, 'pinterest', p.pinterest, 'whatsapp', p.whatsapp,
           'threads', p.threads)), '{}')              AS socials,
       coalesce(p.emails, '{}')                       AS emails,
       coalesce(p.phones, '{}')                       AS phones,
       p.domain IS NOT NULL                           AS has_profile,
       -- Popularity ranks by linked places, then web footprint; the footprint itself is not exported.
       row_number() OVER (PARTITION BY d.country_code
                          ORDER BY d.business_count DESC, d.crawl_pages DESC NULLS LAST, d.domain)::int AS popularity
FROM gold.domains d
LEFT JOIN gold.domain_profiles p ON p.domain = d.domain AND p.country_code = d.country_code
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
CREATE INDEX ON app_build.websites USING gin (name gin_trgm_ops) WHERE name IS NOT NULL;
CREATE INDEX ON app_build.websites (country_code, has_profile, popularity);

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
           'websites', (SELECT count(*) FROM app_build.websites),
           'website_profiles', (SELECT count(*) FROM app_build.websites WHERE has_profile)) AS row_counts;

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
ANALYZE app_build.category_stats;
ANALYZE app_build.city_category_stats;

-- ---------------------------------------------------------------- swap

DROP SCHEMA IF EXISTS app CASCADE;
ALTER SCHEMA app_build RENAME TO app;
