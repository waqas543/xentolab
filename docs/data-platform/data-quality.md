# Data quality

Quality checks are reusable classes (`quality/checks.py`). Each compiles to one DuckDB query over a dataset version and
reports `records_checked` and `records_failed`.

| Check | Fails a record when |
|---|---|
| `NotNullCheck(column)` | NULL or empty |
| `UniqueCheck(columns)` | it duplicates another row's key |
| `RangeCheck(column, min, max)` | outside the range |
| `RegexCheck(column, pattern)` | doesn't fully match |
| `CoordinateCheck(lat, lon)` | missing, out of range, or (0, 0) |
| `DomainCheck(column)` | not a valid hostname |
| `PhoneCheck(raw, normalized)` | a raw phone exists but couldn't be normalized |
| `RecordCountCheck(min, max, max_change_pct)` | the count is out of bounds or changed more than X% vs the previous version of the partition |
| `SchemaCheck(required)` | a required column is missing or has the wrong type (extra columns are fine) |
| `FreshnessCheck(column, max_age_days)` | the newest value is too old |
| `DuplicateRateCheck(columns)` | it shares its key with other rows |

Each check has a `severity` (`error` or `warn`) and a `threshold_pct` (the allowed failure percentage):

- Error checks above their threshold **fail the stage** with `QualityGateError`, which is not retried. Results are
  stored first.
- Warn checks are recorded for the dashboards.
- A check that itself crashes records `status = "error"`. It is never silently passed.

## Where checks come from

- **Bronze:** `adapter.quality_checks("bronze", dataset_id)`. For OSM: unique ids, non-empty, schema.
- **Silver and Gold:** shared suites in `quality/suites.py`, including the `silver.places` contract, unique ids,
  valid coordinates, categories present, row-count stability (±25 % vs the previous version), and informational
  coverage checks.

## Configurable thresholds

Overrides are stored per pipeline in `dp.pipeline_definitions.quality_overrides` and set through
`PATCH /api/data-platform/pipelines/{id}`:

```json
{"silver.places": {"row_count_stable": {"threshold_pct": 0, "severity": "warn"},
                   "name_present": {"threshold_pct": 20}}}
```

## Results

Every result is a row in `dp.quality_results`: run, stage run, stage, dataset, dataset version, check, type,
severity, status, records checked and failed, failure %, threshold % and details. Re-running checks on an unchanged
dataset version is skipped as idempotent.

The Quality dashboard combines three things:
- Per-source coverage computed from Silver: missing names, categories, addresses and websites; invalid domains and
  phones; places outside known boundaries.
- Duplicate candidates (suggested matches), conflicting Gold records and rejected records.
- The latest result of every check.

Every tile drills down to the matching records in the Explorer.

## Data quality rules applied in the pipeline

These rules fix problems found by profiling the live data (November 2026). Each has a check in
`quality/suites.py`, so a regression shows on the quality dashboard.

| Problem | Where it is fixed | Rule |
|---|---|---|
| Foreign search results (Google returned Dubai, Mumbai and Caribbean businesses for Pakistani searches) | `sources/googlemaps/silver.py` | A place outside every boundary of the searched country is rejected as `outside_country`. |
| Records stacked on a town or country centre (Overture put 1,700 Karachi listings on one point) | `silver_contract.location_precision` | 10+ records of one source on the identical point, or 2+ on a gazetteer place centre, are `location_precision = approximate`. They take the city the provider states. Gold uses an exact member's position when one exists. The app leaves them off maps, nearby results and distances. |
| Rawalpindi absorbing Islamabad, Karachi absorbing Hub | `geo.py` | A city reaches as far as its population suggests, but never across a first-level boundary. A small first-level unit named after a city inside it (Islamabad Capital Territory) belongs to that city. Population is the largest any gazetteer gives. |
| Same-named villages counted as one city | `geo.py`, `gold/builder.py` | Places carry `city_id` (the gazetteer id). Cities are counted and linked by id. |
| ~4,000 overlapping source categories | `categories.py` | Every label maps to one of ~130 canonical categories, each in one group. The source label is kept as `subcategory`. Strong name signals ("Marriage Hall", "Brick Kiln") override wrong labels. Non-businesses (libraries, peaks, shrines) are dropped. |
| Advert-style names (`Ressichem \| Epoxy \| Plaster`, phone numbers, ALL CAPS, mixed scripts) | `normalize.clean_name` | Gold `name` is the display name, `original_name` the listing, `name_local` the local-script spelling. |
| Chains named after one branch ("The Smart School Nasir Bagh Campus Peshawar") | `gold/organizations.py` | A chain with 3+ locations is named after what its branches share (`normalize.chain_name`), or after the Wikidata item its brand tag points to. One brand under two keys (Shell ×2) is merged. |
| 34,000 Wikidata government schools listed as companies | `gold/organizations.py` | A school or public body that no location links to is kept only when the record describes an organization (website, staff, registry, news, industry). |
| Empty-string industries; raw Wikidata industry text | `gold/organizations.py` | Industry is the canonical label of the organization's locations, else its stated industry mapped onto the same list. |
| Social pages counted as websites | `gold/builder.py` | `website` is the business's own site only; a Facebook / WhatsApp / Linktree link is `social_url`. `website_kind` marks sites shared by 5+ locations. |
| Head-office and shared numbers | `gold/builder.py` | `phone_kind`: `hotline` (UAN / toll-free pattern), `shared` (5+ listings, 3+ names), `direct`. |
| Invalid postal codes | `normalize.normalize_postal_code` | Kept only in the country's format (Pakistan: 5 digits). |
| Opening hours lost | `normalize.parse_google_hours` | Google "Open 24 hours" and full weekly schedules become OSM `opening_hours`; status snapshots ("Closes 10 PM") are ignored. |
| Raw star averages reward few reviews | `scripts/app_schema.sql` | `rating_score` pulls each rating towards the country mean by 20 virtual reviews; used for ranking. |
