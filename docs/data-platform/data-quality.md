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
