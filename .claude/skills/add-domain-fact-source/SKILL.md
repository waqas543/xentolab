---
name: add-domain-fact-source
description: Add a new data source that contributes facts about the companies behind web domains (market cap, employees, revenue, industry, socials, contacts, summary...) through raw -> Bronze -> silver.domain_facts -> gold.domain_profiles -> app. Use when asked to enrich domains/websites/company profiles from a new provider (stock exchange, business directory, registry, API, CSV), or to add a new kind of domain fact.
---

# Add a domain-fact source

Every source reaches Gold the same way:

```
provider data (raw, unchanged)
  -> artifact
  -> bronze.<source>_*
  -> silver.domain_facts            (the shared format)
  -> GOLD_BUILD merges all sources  -> gold.domain_profiles
  -> serving -> app.websites -> /api/app/websites -> mobile
```

You write only the adapter. Gold, publishing, the app schema and the API pick the new facts up automatically.

Read before starting:
- `backend/data_platform/sources/websites/`: the working example (adapter.py + silver.py)
- `backend/data_platform/silver_contract.py`: `DOMAIN_FACTS_SCHEMA`, `DOMAIN_FACT_FIELDS`, `DOMAIN_FACT_METHODS`
- `backend/data_platform/gold/domain_profiles.py`: `PRECEDENCE` (how methods rank per field)
- `docs/data-platform/adding-a-source.md` ("A source of domain facts") and `medallion-architecture.md`

If the user is still exploring the idea, discuss the design first and don't edit files.

## Steps

1. **Choose identity and method.**
   - `source_id`: a short provider name, e.g. `psx`. This is the `source` column.
   - `method`: how the provider knows each fact.
     - Registry, exchange or knowledge-base data is `record`.
     - Scraped HTML follows the website source's methods (`jsonld`, `meta`, `link`, `text`, `llm`, `site`).
   - The method sets the rank against other sources. Don't inflate it.

2. **Create `backend/data_platform/sources/<source_id>/adapter.py`**, a `DataSourceAdapter` modelled on `WebsitesAdapter`:
   - `pipelines()`: one `PipelineSpec(id="<source_id>.<name>", parameters={"country": ...}, required=["country"])`.
   - `discover_available_datasets` / `latest_release`: a cheap freshness marker. Make it a checksum string that changes when the provider data changes.
   - `download`: save the provider's data **unchanged** with `stage_file` + `store_artifact`. Reuse identical artifacts with `reuse_artifact`.
   - `validate_download`: `verify_artifact` + a structural check (row count, required keys).
   - `ingest_bronze`: `DatasetVersionWriter` into `bronze.<source_id>_<thing>`, partition `{"country", "artifact_id"}`, keeping the provider's columns plus `lineage_sql(ctx, artifact, source)`. Declare the dataset in `datasets()`.
   - `transform_silver`: write `silver.domain_facts` in partition `{"source": "<source_id>", "country": country}`, short-circuiting with `silver_is_current(...)`.
   - `quality_checks`: Bronze only (not-empty + schema). The Silver contract checks are shared.
   - `collect_metrics`: counts from `silver.domain_facts` for this source.

3. **Map to the contract** in `silver.py`:
   - Column order must match `DOMAIN_FACTS_SCHEMA`.
   - Key every row by registrable domain: `normalize.normalize_website(url)[1]`. Providers without websites must be resolved to domains first, for example via `gold.organizations` / `gold.domains`.
   - `field` must be in `DOMAIN_FACT_FIELDS`. Count unmappable rows and report them as `records_rejected`; don't write them.
   - Normalize values:
     - phones: `normalize_phone(value, country)` (E.164)
     - emails: lower-case
     - social URLs: `normalize.social_profile(url)` gives `social.<network>` + the canonical URL
     - `founded_year`: a 4-digit year
   - Fill `evidence` (the raw value or quote), `evidence_url`, `evidence_type` (`record` for records), `confidence` and `observed_at`.

4. **New kind of fact?** Only if no existing field fits:
   - add it to `DOMAIN_FACT_FIELDS`,
   - add a `PRECEDENCE` entry and a `PROFILE_SCHEMA` column in `gold/domain_profiles.py`,
   - add a serving migration (`migrations/serving/NNNN_*.sql`) adding the column to `gold.domain_profiles`,
   - add the column to `gold/publish.py: SERVING_TABLES`,
   - expose it in `scripts/app_schema.sql` (websites), `backend/xento_api/routes.py` (`website()`), `mobile/src/lib/api.ts` and `WebsiteScreen.tsx`,
   - bump `gold/version.py: GOLD_VERSION`.

   Never expose provenance in the app: `scripts/app_schema.sql` has a guard against it.

5. **Tie-breaks:** add the source to `gold/builder.py: DOMAIN_FACT_SOURCE_PRIORITY`. Higher wins between sources with the same method.

6. **Register:** add `"data_platform.sources.<source_id>.adapter:<Class>"` to `sources/registry.py: BUILTIN_ADAPTERS`, then run `cd backend && ../.venv/bin/python -m data_platform sync`.

7. **Test.** In `tests/integration/test_other_sources.py` (or a new module using the same `runs` fixture):
   - serve a tiny fixture of provider data,
   - run the pipeline with `run(...)`,
   - assert the `silver.domain_facts` rows and the merged `gold.domain_profiles` row, including `field_sources`.

   See `test_gold_domain_profiles_merge_every_source`. Also add the Bronze dataset to `query/registry.py` (Data Explorer) and to `test_explorer_queries_website_datasets`. Then add a Makefile target `pipeline-<source_id>`.

## Verify

```bash
cd backend && ../.venv/bin/python -m pytest tests/unit/test_scrape.py tests/integration/test_other_sources.py -q
make pipeline-<source_id> COUNTRY=PK      # real run: rebuilds Gold for the country (check disk first: df -h /)
psql -d ds_companies -c "select domain, field_sources from gold.domain_profiles where '<source_id>' = any(sources) limit 5"
make app-schema                           # refresh what the mobile app serves
```

A Gold build rewrites every Gold dataset of the country (~0.5 GB for PK). Check free disk before a real run.
