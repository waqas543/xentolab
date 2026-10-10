# Frontend

Next.js 16 / React 19, in `frontend/src/app/data-platform/`. Shared pieces are in `components/dp/`; the API client
and types are in `lib/dp.ts`.

| Route | What it shows |
|---|---|
| `/data-platform` | Dashboard with tabs: Overview (KPIs, records per layer, and "What each source contributes": for every registered source its Silver records, businesses it found, businesses it enriched, organizations and domains), Sources, Organizations, Geography, Categories, Domains, Quality, Operations. Sources also shows how reference sources (Wikidata, OpenCorporates, GDELT, Common Crawl) enrich each location source. Organizations shows registry and knowledge-base coverage, the largest organizations, news mentions, organization links and "in the news, not in Gold" leads. Geography adds cities by businesses per 10,000 residents. Domains adds crawl coverage, domain types, languages and discovery leads. |
| `/data-platform/sources` | Source cards: status, last success and failure, latest artifact and version, records, storage, schedules, and credential status (configured or missing; never the value). Actions: Run, Configure, View runs, View data (opens the source's own Silver dataset), Disable. |
| `/data-platform/runs` | Runs table with filters (source, pipeline, status, country, trigger, date range); refreshes while runs are active |
| `/data-platform/runs/[id]` | Live run detail: stage timeline with progress, logs, quality results, errors and tracebacks, lineage, parameters and versions. Cancel and Retry. |
| `/data-platform/explorer` | Data Explorer across Bronze, Silver, Gold and matches: filter builder, search, column chooser, server-side sort and paging, CSV export, saved views, record drawer with provenance and match review |

## One filter language everywhere

- **One DSL.** The Explorer filter builder, the dashboard's global filters and every drill-down link use the same
  JSON filter tree (`{operator, conditions:[{field, operator, value} | group]}`). The backend validates it against
  the dataset's column metadata and compiles it to parameterized SQL (`query/dsl.py`).
- **Drill-down.** A click on "Businesses without website" builds `explorerHref("gold.businesses", globalFilters,
  [{field: "has_website", operator: "equals", value: false}])`.
- **Metadata-driven UI.** `GET /datasets/{id}/filters` returns field, label, type and allowed operators.
  `POST /datasets/{id}/values/{field}` returns value suggestions computed server-side under the current filters. The
  UI has no hard-coded per-dataset filters.

## Live updates

The run detail subscribes to `GET /runs/{id}/events` (server-sent events, one snapshot whenever the run or a stage
changes). If the stream fails, or an API key is configured (EventSource cannot send headers), it falls back to polling
every 2 s. The runs list polls every 3 s while any run is active.

## Rules the UI follows

- Percentages appear only when the backend reports a real total. Otherwise it shows processed count, rate and elapsed
  time.
- The browser never receives large result sets. Tables are paginated server-side; the map shows aggregated grid cells,
  not points.
- Status is always shown as an icon plus a label, never color alone. Charts follow the dataviz palette (validated for
  colour-vision deficiency), and every chart has a table view.
- Pipeline parameters render from the pipeline's JSON schema (`components/dp/param-form.tsx`).

## Auth

With `DATA_PLATFORM_AUTH_MODE=api_key`, the client sends the key from `localStorage["dp-api-key"]` as `X-API-Key`. A
401 or 403 shows the API error.
