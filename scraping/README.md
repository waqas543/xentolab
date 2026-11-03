# Xento scraping engine

A standalone, modular scraping platform: **fetch → parse → enrich → validate → write**, driven by YAML job
configs. The first job enriches Pakistani companies from FBR raw data. For each company it:

- searches Google (DuckDuckGo is available as a fallback engine but disabled) and keeps Google's AI Overview
- detects the official website and crawls a few of its pages
- fetches the company's public LinkedIn page
- builds a company profile and summary from the website and LinkedIn text with a local Qwen model (Ollama)
- writes contacts, domains and metadata as JSONL

## Architecture

```
config/jobs/<job>.yaml ─► JobConfig (pydantic)
                              │
input/*.jsonl|csv ─► InputReader (field mapping, name resolution) ──► skipped.jsonl
                              │
                              ▼
                 Job plugin (fbr_company_enrichment | site_crawl | …)
                              │
            Fetcher ─► PageLoader interface (rate limit + retries live here)
                              ├─ LoadPageClient  hosted load-page API, render_js=true   (default)
                              ├─ ScrapeApiClient self-hosted /v1/scrape API (Google search)
                              ├─ HttpLoader      plain httpx GET
                              └─ JsLoader        local Playwright (optional extra)
                              ▼
            Parsers (registry): search_result_parser · contact_extractor · metadata_extractor · html_parser
                              ▼
            Enrichers: SearchEnricher (GoogleEngine, DuckDuckGoEngine; fallback or all) ─► DomainDetector
                       ─► CompanyEnricher (crawls the likely website)
                              ▼
            pydantic output records ─► OutputManager (validation) ─► JsonlWriter (1,000 records per file)
                              ▼
     output/data/<prefix>_0001.jsonl …              output/state/<job>.sqlite (resume + de-duplication)
```

| Package | Responsibility |
|---|---|
| `app/core` | config schema, pydantic models, structured logging, exceptions, input reading, SQLite state, utils |
| `app/loader` | `PageLoader` interface + `LoadPageClient`, `ScrapeApiClient`, `HttpLoader`, `JsLoader` |
| `app/fetch` | token-bucket `RateLimiter`, tenacity `RetryPolicy`, job-facing `Fetcher` (stats, in-run cache) |
| `app/parse` | `HtmlDocument` (selectolax) and the parser plugins |
| `app/enrich` | search engines (Google, DuckDuckGo) with fallback, domain scoring, per-company orchestration |
| `app/crawl` | URL frontier, bounded-concurrency scheduler, policy-driven crawler |
| `app/writer` | writer plugins (`jsonl_writer`) and the validating `OutputManager` |
| `app/jobs` | job plugins and the shared run lifecycle (`BaseJob`) |

Every stage is a plain class with small inputs and outputs, so each one can be tested on its own. Only the
parsers, enrichers and jobs are job-specific. Loaders, the fetcher, the writer and state are shared by all jobs.

## Setup

```bash
cd scraping
uv venv --python 3.14 .venv          # or: python3.11+ -m venv .venv
uv pip install --python .venv/bin/python -e ".[dev]"
cp .env.example .env                 # then set LOAD_PAGE_URL/LOAD_PAGE_API_KEY and SCRAPE_API_URL/SCRAPE_API_KEY
```

`LOAD_PAGE_URL` must be reachable from the machine running jobs. If it isn't, `test-loader` reports a
connection error. Secrets are read only from the environment or `.env`. They never go into YAML, logs or output.

Optional local JS rendering: `uv pip install -e ".[js]" && .venv/bin/playwright install chromium`, then use
`loader: playwright`.

## Commands

Run from `scraping/` (or use the `scrape` entry point installed by `pip install -e .`):

```bash
python -m app.cli list-jobs
python -m app.cli validate-input --job fbr_company_enrichment     # no network: rows, rejects, samples
python -m app.cli test-loader --url "https://example.com"         # status, latency, attempts, final URL
python -m app.cli test-loader --url "https://example.com" --loader http --save /tmp/page.html
python -m app.cli run-job --job fbr_company_enrichment --limit 10 # first 10 new companies
python -m app.cli run-job --job fbr_company_enrichment            # safe to re-run: finished rows skipped
python -m app.cli resume --job fbr_company_enrichment             # continue latest run (same job_id)
python -m app.cli stats --job fbr_company_enrichment              # progress, runs, top errors, files
python -m app.cli run-job --job fbr_company_enrichment --restart  # forget progress (files are kept)
python scripts/run_fbr_enrichment.py --limit 10                   # same job, plain script
```

`--job` also accepts a path to a YAML file. Add `-v` for debug logs. Logs also go to
`output/logs/<job>.log` as JSON lines.

## Resuming and de-duplication

- **Unit of work:** for the FBR job, one company, keyed by its normalized name. Its records are written and
  `fsync`ed first, and only then is it marked done in `output/state/<job>.sqlite`.
- **On restart:** companies marked `success`, `no_results`, `partial` or `skipped` are never reprocessed.
  `failed` ones are retried.
- **Duplicates in the input:** the same normalized name appearing again is written to `skipped.jsonl` with
  reason `duplicate_company`.
- **Duplicate records:** every record has a de-duplication key, for example company key + normalized result
  URL for search results. A record already written is never written again. `record_id` is a UUIDv5 of that
  key, so the same input always produces the same IDs.
- **Interrupted runs:** Ctrl+C closes the files cleanly. On the next open, a line left half-written by a crash
  is truncated away, and numbering continues from the last file.

## FBR company enrichment

1. **Input.** Rows come from `input/fbr_raw_data.jsonl` (or `.csv`).
   - `company_name_field` and `fallback_company_name_field` can each be one field name or a list of aliases.
   - `field_map` maps `ntn`, `registration_no`, `province`, `city` and `address`.
   - Field names match case-insensitively.
2. **Name resolution.** The engine uses `company_name`, falling back to `business_name`. If both are missing,
   the row goes to `skipped.jsonl` with reason `missing_company_name`.
   - The name is cleaned: whitespace trimmed and collapsed, symbols removed.
   - Legal suffixes like `(Pvt.) Ltd.`, `Private Limited` and `LLP` are stripped from the **search** name. The
     suffix is kept if stripping would leave a single word ("Systems Limited").
   - The original name is always kept in the output.
3. **Search.** The engine builds the query from `query_template` (default `{name} {city} Pakistan`) and asks
   the engines in `search.engines`, in order.
   - `mode: fallback` (default): the first engine that answers wins. `mode: all`: every engine is asked and
     the results are merged for domain detection.
   - Each engine has its own loader, rate limit and retries. Google uses the `scrape_api` loader at one search
     every 4 s. DuckDuckGo uses the job's loader.
   - A bot challenge pauses that engine and is retried with backoff, bypassing the cache. If the engine is still
     blocked, it cools down for `cooldown_seconds` (doubling while it stays blocked, up to 8x) and the next
     engine is used. The fallback is written to `errors.jsonl` with the engine name. When no engine is available
     (the FBR job uses Google only), searches wait for the cooldown to end. A company fails only if no engine
     answered.
   - Every organic result is parsed: title, URL, domain and snippet. Ads and duplicates are dropped.
     DuckDuckGo redirect links are unwrapped. Google's `/goto?url=…` links are resolved to the real URL with
     one plain redirect request each (`resolve_links`).
   - **Google AI Overview.** Its text (headings, `- ` bullets, no citation chips) and its cited sources (title,
     URL, site, snippet) go to `fbr_company_search_summaries`.
   - A page in an unrecognized layout is an error, never silently recorded as "no results".
4. **Contacts from snippets and summaries.** Phone numbers, emails and locations are extracted from each
   result's title and snippet, and from the AI Overview.
5. **Domain detection.** Every domain in the results is scored.
   - Signals: name match (exact, contains, token coverage, acronym, fuzzy), TLD preference (`.pk` and `.com.pk`
     first, then `.com`, `.net`, `.org`), rank, title match and repetition.
   - Social, directory, news, marketplace and government domains are categorized and never chosen.
   - The best candidate becomes `likely_domain` only if it relates to the company name and scores at least
     `min_likely_domain_score`.
6. **Website.** The homepage of the likely domain is crawled, plus about/contact pages (`website.max_pages`).
   - Each page gives contacts (visible text, `tel:` and `mailto:` links, social links, JSON-LD) and metadata
     (title, description, canonical URL, language, keywords, Open Graph, Twitter cards, JSON-LD).
   - If website pages fail, the company still succeeds as `partial` and the failures go to `errors.jsonl`.
   - The FBR job fetches website pages without a browser (`render_js: false`).
7. **LinkedIn (`linkedin:`).** The company's public LinkedIn page, main page only. Everything else on
   LinkedIn is behind the login wall.
   - The link comes from the company's own website first. Otherwise it is a search result whose title or slug
     matches the company name. Numeric `/company/<id>` links are skipped, because they only show the login
     page.
   - Fetched without a browser (`render_js: false`) at one request every 2 s.
   - LinkedIn shows the login wall for the same page on some requests and not others, so a walled page is
     retried with backoff. If it is still walled, there is a warning and the company is `partial`.
   - Saved: name, tagline, description, industry, website, size, headquarters, type, founded year,
     specialties, followers, employees on LinkedIn, office addresses, logo and recent posts.
   - `name_matches` checks the page name against the company name. `website_matches_likely_domain` checks
     the page's website against the detected domain.
   - When the name matches, the page text also goes to the company-profile model.
8. **Company profile (`summary:`).** For every company with a likely domain, the text of its crawled pages
   (about, products, team and contact first; homepage after) plus the search snippets, at most `text_budget`
   characters, goes to a local model on Ollama (`Qwen3.5:4B`, temperature 0, JSON schema).
   - It returns a summary, industry, business type, products and services, brands, key people, certifications,
     export markets, locations, partners and clients, plus head-office city, address, founded year,
     employees, legal form, parent company, stock listing, financial info and production capacity.
   - **Evidence guard.** Every single-value fact needs an exact quote from the text, and the value must be in
     the quote. Every list item must appear in the text. Anything else is dropped and listed in `dropped`. The
     quotes are kept in `evidence`.
   - Ollama unreachable: the company fails and is retried on the next run. An unusable answer, twice: a
     warning, and the company is `partial`.
   - Speed: about 15–25 s per company on a laptop, one at a time (`max_concurrency: 1`).

Phone numbers are validated with libphonenumber for the PK region and stored in E.164 format:

| Type | Examples |
|---|---|
| Mobile | `+92 3xx`, `0092 3xx`, `92 3xx`, `03xx-xxxxxxx` |
| Landline | `042-xxxxxxxx`, `(021) …`, `051 …` |
| UAN | `111-xxx-xxx`, with or without an area code |
| Toll-free | `0800` |

NTNs, dates and amounts are not mistaken for phone numbers.

### Output (`output/data/`, at most 1,000 records per file; LinkedIn in `output/data/linkedin/`)

| File prefix | One record per |
|---|---|
| `fbr_company_search_results_NNNN.jsonl` | search result (engine, title, URL, domain, `likely_domain`, snippet contacts, metadata) |
| `fbr_company_search_summaries_NNNN.jsonl` | search summary, e.g. Google's AI Overview (text, sources, source domains, contacts) |
| `fbr_company_domains_NNNN.jsonl` | candidate domain (score, category, reasons, result URLs) |
| `fbr_company_contacts_NNNN.jsonl` | source with contacts (`search_snippet`, `search_summary` or `website` page) |
| `fbr_company_profiles_NNNN.jsonl` | company with a website (LLM profile, summary, evidence quotes, dropped facts, contacts, pages read) |
| `linkedin/fbr_company_linkedin_companies_NNNN.jsonl` | company with a public LinkedIn page (about, size, founded, specialties, followers, locations, recent posts) |
| `fbr_company_metadata_NNNN.jsonl` | website page (title, description, canonical, OG, JSON-LD, fetch info) |
| `fbr_company_enrichment_summary_NNNN.jsonl` | company (status, engines used, summary types, likely domain, LLM summary and industry, merged contacts, counts) |
| `errors_NNNN.jsonl` | failure (stage, engine, error type, message, URL, attempts) |
| `skipped_NNNN.jsonl` | rejected input row (reason, raw row) |

Every record carries `record_id`, `source`, `company_key`, `company_name`, `business_name`, `search_query`,
`scraped_at`, `job_id`, `job_name`, `loader` and `status`. Records are validated with pydantic before they are
written. Files are UTF-8, with non-ASCII characters kept as-is (the `ensure_ascii=False` equivalent).
`errors_*` and `skipped_*` are shared by all jobs writing to the same `output_dir`; filter them by `job_name`,
or give them job-specific names with `outputs:`.

## Job configuration

Your original flat keys (`input_file`, `company_name_field`, `rate_limit`, `retry`, `parsers`, …) are valid
as-is. The full schema is `JobConfig` in `app/core/config.py`; `config/jobs/fbr_company_enrichment.yaml` shows
every section.

| Key | Purpose |
|---|---|
| `job_name` / `job_type` | config name / registered job class (defaults to `job_name`) |
| `input_file`, `input_format`, `company_name_field`, `fallback_company_name_field`, `field_map` | input + mapping |
| `name_normalization` | `strip_suffixes`, `extra_suffixes`, `min_words_after_strip`, `remove_chars` |
| `search`, `max_results_per_company` | query template, search URL template, region, pages |
| `website` | crawl of the likely domain: `enabled`, `max_pages`, `max_depth`, include/exclude regexes |
| `seed_urls`, `crawl` | generic crawling: depth, page budget, `allowed_domains`, include/exclude regexes |
| `loader`, `render_js`, `timeout_seconds` | `load_page` (default) / `http` / `playwright` |
| `rate_limit` | `requests_per_second`, `max_concurrency` (shared by every request in the run) |
| `retry` | `max_attempts`, `backoff_seconds`, `max_backoff_seconds`, `retry_on_status` |
| `parsers`, `writers` | registered names or `package.module:ClassName` |
| `output_dir`, `output_prefix`, `outputs`, `max_records_per_jsonl_file`, `state_dir` | output layout |
| `extraction` | `min_likely_domain_score`, `extra_generic_domains`, `extra_cities`, `extra_address_keywords`, `region` |
| `options` | free-form settings for custom jobs |

Default extraction rules are in `config/extraction_rules.yaml`: cities, provinces, address keywords, legal
suffixes and generic domains. You can extend them there or per job under `extraction:`.

## Adding a new scraping job

**Crawl-and-extract jobs need no code.** Copy `config/jobs/example_site_crawl.yaml`, set `seed_urls`, the
`crawl:` policy and `parsers:`, then run it. Each parser's output goes to its matching file:

- `ContactInfo` results go to `<job>_contacts_*`.
- `PageMetadata` results go to `<job>_metadata_*`.
- Anything else goes to `<job>_pages_*` under `data.<parser name>`.

**Custom parser.** Write a parser class, then list `price_parser` in a job's `parsers:`:

```python
# app/parse/price_parser.py  (import it in app/parse/__init__.py)
from app.parse.base_parser import BaseParser, register_parser
from app.parse.html_parser import HtmlDocument, node_text

@register_parser
class PriceParser(BaseParser[dict]):
    name = "price_parser"

    def parse(self, page):
        doc = HtmlDocument(page.html, page.final_url)
        return {"price": node_text(doc.css_first(".price"))}
```

You can also skip registration and reference it as `parsers: ["app.parse.price_parser:PriceParser"]`.

**Custom job (custom input or orchestration).** Subclass `BaseJob`, implement `execute()`, and register it:

```python
@register_job("my_job")
class MyJob(BaseJob):
    async def execute(self, ctx, *, limit=None):
        page = await ctx.fetcher.fetch("https://example.com")
        records = [...]                               # pydantic records (subclasses of BaseRecord)
        self.commit(ctx, "item-key", RecordStatus.SUCCESS, records, seen=[("pages", "dedup-key")])
```

Import the module in `app/jobs/__init__.py` and set `job_type: my_job` in the YAML. Run IDs, resume, state,
rate limiting, retries, validation, rotation and stats come for free. A new record type is a `BaseRecord`
subclass with a `record_type`; the writer creates `<prefix>_<record_type>_NNNN.jsonl` automatically.

**Custom loader** (e.g. a self-hosted ScrapingBee-style renderer). Subclass `PageLoader`, implement
`_fetch_once()` (one attempt: raise `TransientLoaderError` for retryable failures and `LoaderError` for
permanent ones), and add it to `create_loader` in `app/loader/__init__.py`. Retries, rate limiting and logging
are inherited.

## Tests

```bash
.venv/bin/python -m pytest -q      # no network: loaders are mocked, HTML comes from tests/fixtures
.venv/bin/ruff check .
```

The tests cover:

- `LoadPageClient` request shape, retries (timeout, 5xx, 429 Retry-After, empty body) and non-retryable errors
- `ScrapeApiClient` request shape, challenge and rate-limit backoff, wait timeouts and rejected keys
- rate limiting
- company-name resolution and normalization
- JSONL rotation at 1,000 records, restart continuation and torn-line repair
- DuckDuckGo HTML, JS and bot-challenge pages (including retry after a challenge)
- Google results, AI Overview text and sources, captcha pages, `/goto` link resolution
- engine fallback, cooldown after a block, and `mode: all`
- the profile evidence guard, context building, the Ollama client (retry, outage) and profiles in the job
- LinkedIn page parsing, the login wall (retry, give up), picking the right page, and its output folder
- Pakistani phone formats and false positives
- emails, social links, locations and metadata
- domain detection
- an end-to-end job with resume, and one with Google summaries and a DuckDuckGo fallback

## Notes

- **Google rate-limits searches.** Bursts from one IP get a reCAPTCHA (the scrape API reports
  `TARGET_CHALLENGE`). Keep Google at about one search every 4 s. While blocked, Google-only jobs pause for the cooldown.
  Google uses `wait_until: networkidle`. If it times out (`WAIT_CONDITION_TIMEOUT`), try `load`. The API allows
  at most 60 s per request.
- **DuckDuckGo blocks plain HTTP clients quickly.** It returns a 202 bot challenge, which the parser detects.
  Use the `load_page` loader (proxies and rendering) for real runs, and keep `requests_per_second` low.
- **Certificates.** `HttpLoader` verifies TLS against the operating system's trust store (`truststore`). Many
  Pakistani sites send incomplete certificate chains that the bundled `certifi` store rejects.
- **Generic crawl jobs** de-duplicate records across runs but re-fetch pages, because links are rediscovered
  from them.
