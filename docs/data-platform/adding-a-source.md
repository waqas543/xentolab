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
