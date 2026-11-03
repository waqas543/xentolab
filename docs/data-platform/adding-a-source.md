# Adding a source

`sources/overture/` is a complete, working example (Milestone 2); the walkthrough below follows it. For sources
that are not places, see `sources/wikidata/` (organization records), `sources/commoncrawl/` (web domains),
`sources/gdelt/` (signals, incremental windows) and `sources/opencorporates/` (credentials and quotas). All of them
compose the helpers in `sources/common.py`. Everything happens under `backend/data_platform/sources/overture/`;
the engine, ER, Gold, APIs and UI need no changes.

1. **Create the adapter.**

   ```python
   class OvertureAdapter(DataSourceAdapter):
       source_id = "overture"
       name = "Overture Maps"
       license = "CDLA-Permissive-2.0 / ODbL (per theme)"
       capabilities = SourceCapabilities(supports_release_detection=True, provides_places=True)
       parser_version = "overture-bronze-1"
       transformation_version = f"overture-silver-1+{NORMALIZER_VERSION}+{TAXONOMY_VERSION}"

       def pipelines(self):
           return [PipelineSpec(id="overture.places", name="Overture places", description="…",
                                parameters={"country": {"type": "string", "pattern": "^[A-Za-z]{2}$"},
                                            "release": {"type": ["string", "null"], "default": None}},
                                required=["country"])]
   ```

2. **Discover and download.** Use the official distribution: the release listing plus GeoParquet files. Store files
   through `ctx.storage` (never overwrite) and record a `dp.dataset_artifacts` row with SHA-256 and `source_version`
   (the release id). Use `data_platform.http` for retries, resume and error classification.

3. **Bronze.** Keep the provider's columns. For Overture, the GeoParquet is already columnar, so Bronze can be a
   catalogued copy partitioned by release and country. Keep unknown fields: write with the provider schema and rely
   on `union_by_name` for evolution. Register datasets in `datasets()` (e.g. `bronze.overture_places`).

4. **Silver.** Map onto the `silver.places` contract (see [medallion-architecture.md](medallion-architecture.md)):
   - `silver_id = "overture:<gers id>"`
   - Categories go through a new `classify_overture()` in `taxonomy.py`, returning the shared `category_group`.
   - Use `normalize.normalize_website/phone/name_key` and `geo.GeoIndex` for admin and city fields.
   - Put the provider confidence in `source_confidence`.
   - Write the partition `{"source": "overture", "country": …}` with `DatasetVersionWriter`, and send unusable rows
     to `silver.rejects`.

5. **Quality.** Return checks from `quality_checks()` for your Bronze datasets. Silver and Gold suites are shared.

6. **Priority.** Add the source to `gold/builder.py: SOURCE_PRIORITY` to place it in survivorship.

7. **Register.** Add the class path to `BUILTIN_ADAPTERS`, or set `DATA_PLATFORM_EXTRA_SOURCES` for an external
   package. Then run `python -m data_platform sync`.

8. **Test with a tiny fixture.** Build a few rows of provider-shaped Parquet in a test fixture and run the pipeline
   through `run_to_completion` (see `tests/integration/test_osm_pipeline.py`). CI must not download real releases.

A source that describes organizations writes `silver.organizations` instead of `silver.places`. Entity resolution then
links it to places and to other organization records (`org-v1`), and Gold folds it into `gold.organizations`. Domain
and signal sources write `silver.web_domains` or `silver.org_mentions`. All of these contracts are in
`silver_contract.py`, and Gold picks them up automatically.

Once a second source writes `silver.places` for the same country, entity resolution compares records across sources
automatically. Gold `sources`, `source_count`, overlap analytics and the Sources dashboard then light up without
further work.

## A source of domain facts

Use this when a source knows something about **the company behind a website**: market cap from a stock exchange,
employees from a directory, a summary from a profile site. It writes `silver.domain_facts`, and Gold merges it into
`gold.domain_profiles`, which the app shows on the website screen. Nothing outside the adapter changes.
`sources/websites/` is the working example. The skill `.claude/skills/add-domain-fact-source` walks through the same
steps as a checklist.

1. **Raw first.** Download or export the provider's data unchanged as the artifact (`store_artifact`), and keep it in
   a Bronze dataset with its own columns plus `lineage_sql(...)`. Bronze is what lets Silver be rebuilt when the mapping
   changes.

2. **Key by domain.** Every fact needs the registrable domain. Use `normalize.normalize_website(url)[1]`. If the
   provider has company names but no websites, resolve them to domains through `gold.organizations` or
   `gold.domains`, and record the method as `record`.

3. **Map onto the contract** in `transform_silver`, into partition `{"source": "<source_id>", "country": …}`:

   ```sql
   SELECT domain, '{country}' AS country_code,
          'market_cap' AS field, format_market_cap(cap) AS value,        -- a field from DOMAIN_FACT_FIELDS
          'psx' AS source, 'record' AS method,                          -- a method from DOMAIN_FACT_METHODS
          cap_raw AS evidence, listing_url AS evidence_url, 'record' AS evidence_type,
          0.9 AS confidence, as_of::TIMESTAMPTZ AS observed_at, symbol AS source_record_id,
          artifact_id, {bronze_id} AS bronze_version_id, {run_id} AS pipeline_run_id,
          '{transformation_version}' AS transformation_version, now() AS ingested_at
   FROM bronze
   ```

   - Normalize values the way the contract expects: E.164 phones (`normalize_phone`), lower-case emails, canonical
     profile URLs (`normalize.social_profile`), a plain four-digit year for `founded_year`.
   - Count facts you cannot map instead of writing them. The SILVER_QUALITY suite fails a version with unknown fields
     or methods.
   - For a genuinely new kind of fact, add the field to `DOMAIN_FACT_FIELDS`, a precedence entry in
     `gold/domain_profiles.py: PRECEDENCE`, a column in `PROFILE_SCHEMA`, a serving migration, and `publish.py`.

4. **Precedence.** Pick the method honestly: the method decides where your values rank against other sources. If
   several sources state a fact the same way, `gold/builder.py: DOMAIN_FACT_SOURCE_PRIORITY` breaks the tie, so add
   your source there.

5. **Register and test.** Add the adapter to `BUILTIN_ADAPTERS`, then run `python -m data_platform sync`. Add an
   integration test that runs the pipeline on a fixture and asserts the merged `gold.domain_profiles` row, as
   `test_gold_domain_profiles_merge_every_source` does.

Sources that already write `silver.organizations` (Wikidata, OpenCorporates) need nothing extra. Their records that
state a website become `record` facts automatically: name, description, industry, HQ city, inception, employees,
contacts and social links.
