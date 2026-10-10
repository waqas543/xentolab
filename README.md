# OSM Companies

A local data platform for discovering companies, businesses, places and domains from open data, plus the original
OSM companies dashboard and SQL query runner.

## Data platform (start here)

Source adapters → Bronze / Silver / Gold → entity resolution → dashboards and Data Explorer, with run history, retries,
cancellation, lineage and data quality.

| Source | Role | Credentials |
|---|---|---|
| OpenStreetMap | business locations, boundaries | – |
| Overture Maps | business locations (phones, websites, confidence) | – |
| GeoNames | gazetteer: every city, town and village, population, postal codes | – |
| Wikidata | organizations, brands, institutions; identifiers, industries, HQs | – |
| Common Crawl | web-domain discovery for the ccTLD, crawl statistics | – |
| GDELT | organizations in the news (incremental) | – |
| OpenCorporates | company-registry records via the official API | your own token in `DP_SECRET_OPENCORPORATES_API_TOKEN` |

```bash
make setup     # once: Python + Node deps, migrations, register sources
make up        # starts API (:8000), worker and web app (:3000) in one terminal — Ctrl+C stops all
```

Then open http://localhost:3000/data-platform and run a pipeline from the Sources page, or from a second terminal:

```bash
make pipeline-osm-pakistan PBF=~/Downloads/pakistan-261007.osm.pbf   # or omit PBF to download from Geofabrik
make pipeline-all COUNTRY=PK    # queue every keyless source (GeoNames, OSM, Overture, Wikidata, Common Crawl, GDELT)
make test
make help      # all commands
```

`make up` checks prerequisites (virtualenv, node_modules, PostgreSQL, free ports), applies migrations, prefixes each
service's logs, and stops everything if any service exits. Options: `API_PORT=8001 WEB_PORT=3001 WORKERS=2 make up`.

Documentation is in [docs/data-platform/](docs/data-platform/architecture.md): architecture, pipeline lifecycle,
storage, source adapters, medallion layers, entity resolution, data quality, frontend, operations, and adding a source.

---

## v1: OSM companies dashboard

The sections below describe the first version: a direct PostgreSQL import, `osm_app.companies`, and the dashboard
pages under "OSM companies (v1)". The data platform supersedes this ingestion path; the v1 pages keep working.

```
import_osm.py        PBF  ->  osm_raw.*            (raw nodes / ways / relations)
build_companies.py   osm_raw.*  ->  osm_app.*      (companies, admin areas, query-runner tables)
backend/             FastAPI  (Python)  :8000
frontend/            Next.js 16 + React 19  :3000
```

## 1. Data

```bash
python -m venv .venv && .venv/bin/pip install osmium psycopg[binary] -r backend/requirements.txt
psql -d ds_companies -f schema.sql
.venv/bin/python import_osm.py pakistan-latest.osm.pbf
.venv/bin/python build_companies.py          # ~30 s; safe to re-run
```

`build_companies.py` enables the `postgis` and `pg_trgm` extensions and builds:

| Table | Contents |
|---|---|
| `osm_app.companies` | Every **named** feature tagged `shop`, `office`, `craft`, `healthcare`, `industrial`, `company`, `man_made=works`, a business `tourism` value, or an `amenity` that isn't public infrastructure. Nodes keep their location; ways and relations use their centroid. |
| `osm_app.admin_areas` | Country (level 2), province (4) and district (6) polygons assembled from boundary relations |
| `osm_app.saved_queries`, `osm_app.query_history` | Query runner storage |

Derived columns on `companies`:

- `category_group`: one of 11 groups (Retail, Food & Drink, Health, …). The rules are in `CATEGORY_GROUP_SQL`.
- `category`: the tag value that classifies the company (`restaurant`, `bank`, …). `shop=yes` becomes `unspecified_shop`.
- `province`, `district`, `country`: point-in-polygon against `admin_areas`.
- `city`: the nearest `place=city` within a population-scaled radius (Karachi ≈ 35 km, small cities 6 km), otherwise the nearest town. Points inside the Islamabad Capital Territory are always "Islamabad".
- `locality`: the nearest city or town node (neighbourhood level, e.g. "Gulberg"), plus `locality_distance_km`.
- `brand`: English name preferred, unified across scripts by `brand:wikidata`.
- `phone`, `website`, `email`, the `has_*` flags, and `completeness` (0–100, 20 points per contact field).

Pass `--skip-admin-areas` to rebuild only the companies.

## 2. Backend

```bash
cd backend
../.venv/bin/uvicorn app.main:app --reload --port 8000
```

Configure it with `DATABASE_URL` (default `dbname=ds_companies`) and `CORS_ORIGINS` (default `http://localhost:3000`).
API docs are at http://localhost:8000/docs.

## 3. Frontend

```bash
cd frontend
nvm use            # Node 26 (.nvmrc)
npm install
npm run dev        # http://localhost:3000
```

Set `NEXT_PUBLIC_API_URL` if the API is not on `http://localhost:8000`. See `.env.example`.

## Features

**Filters.** One filter bar scopes every page: search, group, category, province, district, city, brand, has phone/website/email/address/hours, chains only, and minimum completeness. Filters live in the URL, so views can be bookmarked and shared, and they carry across pages.

- **Overview.** KPI tiles; companies by group, category, city and province; contact-data coverage; completeness distribution; data freshness by year. Click any bar to drill down. Every chart has a table view.
- **Companies.** A sortable, paginated table and CSV export of the filtered set (streamed, with no row limit). Click a row to open a detail drawer with a mini map, contact links, location, OSM metadata, all raw tags, the 10 nearest companies, and links to openstreetmap.org and the query runner.
- **Map.** Canvas-rendered points for the current view (up to 15k, most complete first). The map zooms to the filtered results, and you can click a point for details.
- **Analytics.**
  - *Pivot heatmap*: any two dimensions, shown as counts, % of row or % of column.
  - *Chains & brands*: locations, cities and provinces per brand, plus phone coverage.
  - *Data quality*: contact coverage per dimension, and likely duplicates (same name within N metres).
  - *Tag explorer*: frequency of every OSM tag key and its top values.
- **Query runner.**
  - Editor: tabbed CodeMirror editor with PostgreSQL highlighting and schema-aware autocomplete. Format SQL, saved queries (stored in the DB), server-side history, and example queries.
  - Running: ⌘/Ctrl+Enter runs the query, or just the selection. Rows (up to 50k) and timeout are configurable, and a running query can be cancelled.
  - Results: a virtualized grid with column types, client-side sort and filter, and a cell inspector for JSON. Results can be charted, copied as TSV or JSON, or exported as a full CSV.
  - Plans: Explain and Explain Analyze.

### Query runner safety

- **Read-only (default).** Each query runs in a `READ ONLY` transaction that is always rolled back. Statements go over Postgres' extended protocol, which rejects multi-statement strings, so `select 1; commit; drop table …` cannot escape the transaction.
- **Streaming.** SELECT queries use a server-side cursor, so only the displayed rows are transferred, even from `osm_raw.nodes` (25M rows).
- **Write mode** must be switched on explicitly, and confirmation is required. It allows multiple statements and **commits**. Destructive statements ask for confirmation again before running.
- The runner connects with the same database role as the dashboard. Use a role with limited privileges in `DATABASE_URL` if the app is shared.
