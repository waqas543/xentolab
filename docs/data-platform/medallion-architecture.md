# Medallion architecture

```mermaid
flowchart LR
  A[(Artifact<br/>original file)] --> B[Bronze<br/>raw, complete, immutable]
  B --> S[Silver<br/>normalized observations<br/>one row per source record]
  S --> E{{Entity resolution<br/>matches + decisions}}
  E --> G[Gold<br/>resolved businesses,<br/>organizations, domains]
  G --> P[(PostgreSQL serving tables)]
```

| Layer | Rule | OSM datasets |
|---|---|---|
| Artifact | The original download, checksummed, never overwritten | `bronze/osm/artifacts/<id>/<file>.osm.pbf` |
| Bronze | Everything the source contains, plus lineage columns. Enough to rebuild Silver without downloading again. | `bronze.osm_nodes`, `bronze.osm_ways` (with ordered `node_refs`), `bronze.osm_relations` (with `members`) |
| Silver | Normalized, typed **observations**. No merging across records. | `silver.places`, `silver.rejects`, `silver.geo_boundaries`, `silver.geo_places` |
| Gold | Business-level entities resolved across sources, with provenance | `gold.businesses`, `gold.business_sources`, `gold.organizations`, `gold.domains`, `gold.domain_profiles` |

## Bronze

- Source-specific tables (no giant generic table). Tags stay as a `MAP<string,string>`; nothing is filtered.
- Lineage on every row: `source`, `artifact_id`, `pipeline_run_id` and `ingested_at`. These are constant per file, so
  they cost almost nothing in Parquet. `source_record_id` is derived from the table and id (`n123`, `w45`, `r6`) and
  materialized in Silver.
- Partitioned by `artifact_id`, so every snapshot is kept side by side until it is explicitly deleted.

## Silver: the cross-source contract

Every place-providing adapter writes `silver.places` with the same core columns. `quality/suites.py` enforces them
with `SILVER_PLACES_CONTRACT`:

- **Identity:** `silver_id` (`<source>:<record>`), `source`, `source_record_id`
- **Names:** `name`, `name_local`, `normalized_name`, `name_key` (the matching key without legal forms), `has_name`
- **Classification:** `entity_type`, `category_key`, `category`, `category_group` (shared taxonomy in
  `taxonomy.py`), `subcategory`
- **Geography:** `country_code`, `admin1`, `admin2`, `city`, `locality`, `latitude`, `longitude`
- **Contact:** `website`, `normalized_domain` (registrable domain; private public suffixes count, so
  `shop.myshopify.com` is its own site), `tld`, `domain_is_platform` (social networks, webmail, link shorteners, site
  builders and marketplaces: an address there is not the business's own domain), `phone`,
  `normalized_phone` (E.164), `email`, `social_links`, the address fields, `opening_hours`
- **Brand:** `brand_name`, `brand_wikidata`, `operator`, `wikidata`
- **Provenance:** `source_confidence`, `source_updated_at`, `source_version`, `artifact_id`, `bronze_version_id`,
  `pipeline_run_id`, `transformation_version`, `ingested_at`
- `attributes`: all original source attributes as JSON, so unknown fields are never lost

Records that cannot be normalized go to `silver.rejects` with a `reason`; they are not dropped silently. Silver is
partitioned by `source` and `country` (the run scope).

The contract schemas live in `silver_contract.py` (`CONTRACT_VERSION`), and both adapters import them.

Geography is normalized by `geo.py`. It assigns country, admin1 and admin2 by point-in-polygon against boundary
polygons, which are built from OSM relations with Shapely `build_area`. It assigns `city` as the nearest city within a
population-scaled radius, and `locality` as the nearest city or town. Boundaries and cities are shared Silver
datasets (`silver.geo_boundaries`, `silver.geo_places`): OSM produces them, and every other source loads them through
`silver_contract.load_geo_index(country)`, so all sources get identical admin and city assignment. When GeoNames is
loaded, its gazetteer takes precedence for populated places (`GEO_PLACE_PRECEDENCE`). Nearest-place lookups use a
spatial tree, because the gazetteer has 150k villages.

Sources that describe something other than a location write their own shared contracts (also in
`silver_contract.py`):

| Dataset | Written by | One row per |
|---|---|---|
| `silver.organizations` | Wikidata, OpenCorporates | organization record (company, brand, institution): names, aliases, legal name, types, industries, website/domain, HQ, coordinates, inception, status, registry id, parent, identifiers |
| `silver.web_domains` | Common Crawl | registrable domain seen by the crawler: pages, hosts, languages, status mix, homepage + its WARC reference, domain type, business-candidate flag |
| `silver.org_mentions` | GDELT | normalized organization name: mentions, articles, outlets, first/last seen, tone, themes, places |
| `silver.domain_facts` | Company websites (scraping engine), and any future fact source | one observed fact about the company behind a domain (see below) |
| `silver.geo_places` | GeoNames, OSM | populated place: kind, population, local name, admin names, alternate names |
| `silver.postal_codes` | GeoNames | postal code with place and admin names |

### `silver.domain_facts`: one format for everything known about a domain

Any source that knows something about the company behind a web domain writes it in this shape, and Gold merges all of
them into `gold.domain_profiles`. Adding a source never touches Gold. See
[adding-a-source.md](adding-a-source.md#a-source-of-domain-facts).

| Column | Meaning |
|---|---|
| `domain`, `country_code` | registrable domain and run country |
| `field` | from the shared vocabulary `DOMAIN_FACT_FIELDS`: `company_name`, `description`, `summary`, `industry`, `category_group`, `city`, `address`, `founded_year`, `employees`, `market_cap`, `revenue`, `financial_info`, `email`, `phone`, `social.<network>` |
| `value` | normalized: E.164 phones, lower-case emails, canonical profile URLs (`normalize.social_profile`) |
| `source` | the provider, e.g. `website` |
| `method` | how it was obtained, from `DOMAIN_FACT_METHODS`: `site` (a custom per-site parser), `jsonld`, `record` (a registry or knowledge-base record), `meta`, `link`, `text`, `llm` |
| `evidence`, `evidence_url`, `evidence_type` | the quote or raw value, the page or record it came from, and the page kind (`home`, `about`, `contact`…, `record`, or `domain` for domain-level facts) |
| `confidence`, `observed_at` | plus the usual lineage columns |

The SILVER_QUALITY suite rejects a version with unknown fields or methods (`quality/suites.py`).

## Gold

- `gold.businesses`: one row per resolved real-world location. Attributes are chosen by survivorship, ranked by source
  priority (`gold/builder.py: SOURCE_PRIORITY`) and then by completeness. `sources`, `source_count`, `member_count`
  and `conflicts` (fields where members disagree) are recorded.
- `gold.businesses.rating` (1-5 stars) and `review_count` come from the sources that rate places (Google Maps, read
  from Silver `attributes.rating` / `attributes.reviews`). When several members are rated, the rating backed by the
  most reviews wins; `field_sources.rating` names its source.
- **Field-level provenance:** `gold.businesses.field_sources` records which source supplied each attribute, e.g.
  `{"name": "osm", "website": "overture", "phone": "osm", …}`. `source_confidence` is the best provider confidence.
  Clusters whose members all have provider confidence below 0.3 stay in Silver but are not promoted to Gold. OSM, with
  no per-record confidence, counts as 1.0.
- `gold.business_sources`: **provenance**. Every Silver record behind each business, with its artifact, run,
  transformation version and match confidence.
- `gold.businesses.enriched_by` lists the **non-location sources** that describe a business: `wikidata` and
  `opencorporates` (through its organization, or the place itself being a Wikidata item, recorded in `wikidata_id`),
  `gdelt` (its organization is in the news) and `commoncrawl` (its website is crawled). This answers "how many OSM
  records were enriched by Wikidata?".
- `gold.organizations`: companies, brands and institutions. They merge what locations imply (brand, shared website)
  with what Wikidata and registries state:
  - **Field-level survivorship:** name from Wikidata, then locations; legal name and registry id from OpenCorporates;
    description, industry, HQ, inception and identifiers from Wikidata.
  - **News signals** from GDELT: mentions, articles, last seen, tone.
  - **Knowledge-base-only organizations** (Wikidata or registry records that no location links to) are kept with
    `location_count = 0`: that's discovery.
  - `field_sources` names the source of each attribute.
- `gold.organization_sources`: **organization provenance**. Every Wikidata or registry record behind an organization,
  with how it was linked (`link_method`: wikidata_id, domain, name_geo, identifier, registry_search, standalone…).
- `gold.domains`: domains used by businesses, stated by organizations, **or discovered by the crawl**. Each has
  business and organization counts, crawl pages, languages, homepage status, domain type and the business-candidate
  flag. Crawl-only domains are leads.
- `gold.domain_profiles`: **the company behind each web domain**, merged from every source's `silver.domain_facts`
  and from organization records that state a website (`silver.organizations`, which become `record` facts). Each
  field takes the first **method** in its precedence list that has a value (`gold/domain_profiles.py: PRECEDENCE`):
  a custom site parser first, then the company's own markup, then registry and knowledge-base records, then meta tags,
  and LLM extraction last, except for the summary, where it comes first. Within a method the most frequent value
  wins, with ties going to the home page and then to the higher-priority source. The city is resolved against the
  gazetteer, so "4K Model Town, Lahore" gives Lahore. `field_sources` records `source:method` per field, for example
  `{"founded_year": "wikidata:record", "summary": "website:llm"}`. The mobile app's `app.websites` serves these
  profiles without the provenance columns.
- `gold.cities`: cities and towns from the preferred gazetteer, with population, business count, website and phone
  coverage, and **businesses per 10,000 residents**. Same-named places attribute businesses to the most populous one.

Gold is partitioned by `country`. A run rebuilds only its own country from **all** sources' Silver for that country.
This is the unit of incremental processing (see [storage.md](storage.md)).

## Reproducibility

Each run records `versions = {parser, transformation, entity_resolution, gold}`. Each dataset version records its
`versions` and its `inputs`: the artifact id with its SHA-256, upstream dataset version ids, and the ER algorithm and
decision fingerprint. A Gold row can therefore be traced back to the exact artifact and code versions that produced it.
`GET /runs/{id}/lineage` returns that graph.
