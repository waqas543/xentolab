# Website enrichment (scraping engine)

Visits each company's own website and collects facts about the company: summary, industry, category group, city,
founded year, employees, social profiles, emails and phones, each with its evidence. The engine only **collects**.
Its facts reach Gold the same way every other source does: through the `websites.profiles` pipeline, as raw data,
then Bronze, then the shared `silver.domain_facts` contract, then `gold.domain_profiles`, then serving, then the
mobile app. Code: `backend/data_platform/scrape/` (engine) and `backend/data_platform/sources/websites/` (pipeline).

```
 crawl (continuous)                         ingest (a normal pipeline run, queued when a campaign completes)
 ─────────────────────────────────          ──────────────────────────────────────────────────────────────────────
 scraping engine ─► web.pages, web.facts ─► artifact ─► bronze.website_facts ─► silver.domain_facts ─┐
                                                                                                    ├─► GOLD_BUILD ─► gold.domain_profiles
       other sources (Wikidata, OpenCorporates, …) ─► silver.organizations ──(records with a website)┘        │
       any future fact source (stock exchange, directory…) ─► silver.domain_facts ──────────────────────────┘
                                                                         serving (Postgres) ─► app.websites ─► /api/app/websites ─► mobile
```

## Architecture

Separate, stateless agents connected by Redis Streams. Each stream is a job queue with one consumer group; start
more copies of an agent to scale that stage.

```
gold.domains ─► frontier ─► scrape:requests ─► fetcher ─► scrape:fetched ─► parser ─► scrape:parsed ─► writer
 (Postgres)        ▲            (jobs)          │ HTML → blob store (disk)              (pages, facts,  │
                   │                            └─ proxies, robots.txt, rate limits      new links)     │
                   └──────────────── web.crawl_urls / web.crawl_domains (Postgres) ◄─────────────────────┘
                   frontier ─► enrich:requests ─► enricher (Ollama) ─► enrich:results ─► writer ─► web.facts
```

| Agent | State | Job |
|---|---|---|
| `frontier` | **the only stateful one**, kept in Postgres `web.crawl_*` | seeds campaigns; turns discovered links into fetch jobs within each domain's page and depth budget; hands finished domains to the enricher; closes campaigns; re-publishes lost jobs when the pipeline is idle; sweeps old blobs. One instance at a time (advisory lock). |
| `fetcher` | none | prepares the request (site headers, proxy, `robots.txt`, one request per host at a time with a delay after each), fetches it, stores the HTML in the blob store, emits a `FetchResult`. Transient failures are retried through the delayed retry set with the next proxy. Never parses. |
| `parser` | none | reads the blob and extracts facts deterministically (JSON-LD, meta tags, social links, `mailto:`/`tel:`, contact details on about/contact pages, clean text, links to follow). Never deletes or writes anything. |
| `enricher` | none | sends the domain's page text to the local LLM; facts come back with evidence quotes and are checked against the text. |
| `writer` | none | the only agent that writes results to Postgres: batched, idempotent upserts; deletes blobs after the commit; rebuilds profiles. |

Job processing (`streams.py`): delivery is at-least-once. A message is acknowledged (and deleted) only after its
handler succeeds. If the handler raises, or the worker dies, the message stays pending, and any worker of the
group reclaims it after `SCRAPE_CLAIM_IDLE_SECONDS`. After `SCRAPE_MAX_DELIVERIES` it moves to `scrape:dlq`. A
`PermanentError`, such as a malformed message, goes to the DLQ at once. Every write is keyed (`request_id =
sha256(campaign:url)`, `(domain, url)` for pages), so processing a message twice is harmless.

Message contracts (`messages.py`) are plain JSON with a version field `v`.
`python -m data_platform.scrape schemas` prints their JSON Schemas, so any agent can be reimplemented in another
language, for example a Go fetcher.

## Running

Needs Redis (`brew services start redis`) and, for the LLM step, Ollama with `Qwen3.5:4B` pulled.

```bash
make data-platform-migrate               # creates web.* and gold.domain_profiles
make scrape-up                           # all agents in one process (development)
make scrape-seed LIMIT=10                # top 10 business-candidate domains of COUNTRY (default PK)
make scrape-seed DOMAINS="marham.pk dvago.pk"
make scrape-status                       # queues, in-progress jobs, DLQ, proxies, blobs, campaign progress
make pipeline-websites                   # facts -> Bronze -> Silver -> Gold -> serving (queued automatically when a
                                         # campaign completes; this runs it now, in the foreground)
make app-schema                          # refresh the mobile app's `app` schema from Gold
make scrape-export                       # data/exports/enrichment/domain_profiles.csv: Gold profiles of the latest campaign
```

In production, run agents as separate processes and scale them independently, for example
`make scrape-agent AGENT=fetcher CONCURRENCY=64` several times, plus one frontier, a few parsers, one enricher per
Ollama instance and one or two writers. They run outside `make up`, so the worker's auto-reload on code changes
never interrupts a crawl.

Other commands (`python -m data_platform.scrape …`): `cancel <campaign>`, `dlq [--requeue]`,
`sweep [--hours N]`, `proxy-check`.

## Where data goes and what is kept

| Data | Where | Lifetime |
|---|---|---|
| Raw HTML | `data/scrape/blobs/` (zstd, one file per fetch) | deleted by the writer as soon as the parsed page is committed; anything left over (dead-lettered, abandoned) is swept after `SCRAPE_BLOB_TTL_HOURS` (48) |
| Crawl state | `web.crawl_campaigns`, `web.crawl_domains`, `web.crawl_urls` | per campaign |
| Page text | `web.pages` (latest per URL, ≤ 20 KB of clean text) | replaced on the next crawl; the LLM reads it |
| Facts (operational) | `web.facts`: one row per fact with the extraction method, evidence page and quote | page facts replaced on re-parse; LLM facts on re-enrichment |
| Artifact + Bronze | `bronze/websites/artifacts/<id>/website-facts-<CC>.jsonl.gz`, `bronze.website_facts` | one snapshot per pipeline run, kept like any source's artifacts |
| Silver | `silver.domain_facts` (partition `source=website`) | current version per country |
| Gold | `gold.domain_profiles` (Parquet version + serving table) | rebuilt by every Gold build for the country |

The fetcher pauses when free disk drops below `DATA_PLATFORM_MIN_FREE_DISK_MB`.

## How the profile is merged

The merge happens in Gold, in `gold/domain_profiles.py`, over **all** sources: these website facts, records from
Wikidata and OpenCorporates that state a website, and any future fact source. Precedence is defined by extraction
method, not by provider. In short: custom site parser, then the site's JSON-LD, then registry/knowledge-base records,
then meta tags, then the LLM. The summary is the exception: there the LLM ranks right after a custom parser. Within a
method the most frequent value wins. City is resolved against the gazetteer. `field_sources` records `source:method`.
Details: [medallion-architecture.md](medallion-architecture.md#gold).

**Guarding against LLM hallucinations** (enricher). City, founded year, employees, market cap and financial info must
come with an exact quote from the page text. The value must also appear in that quote, and the year must be
plausible. Anything else is dropped before it reaches `web.facts`. Summary and industry are interpretive and carry
method `llm`.

Market cap and financials are rarely published on company websites. They come from structured sources instead: add
one as described in [adding-a-source.md](adding-a-source.md#a-source-of-domain-facts).

## Custom crawlers and parsers per company (site plugins)

Add a module to `backend/data_platform/scrape/sites/` with a `Site` subclass that lists the domains it handles. It
is discovered automatically. The frontier, fetcher and parser all resolve the same plugin from the `site` name
carried in every message, so the agents stay stateless. Override only what differs:

```python
from .base import Site

class Daraz(Site):
    name = "daraz"
    version = "1"                       # bump when extraction changes; recorded in web.pages.parser_version
    domains = ("daraz.pk",)
    max_pages = 6                       # crawl budget (default SCRAPE_MAX_PAGES_PER_DOMAIN)
    host_delay_seconds = 5              # be gentler with this host
    headers = {"Accept-Language": "en"}

    def seeds(self, domain):            # pages to fetch first
        return [(f"https://www.{domain}/", "home"), (f"https://www.{domain}/about-daraz/", "about")]

    def follow(self, page_url, hrefs, domain, kind):    # which links to fetch next
        return super().follow(page_url, hrefs, domain, kind)

    def extract(self, page, tree, html, *, url, domain, country, kind):   # add/override facts
        if kind == "about" and (node := tree.css_first(".founded")):
            page["facts"].append({"field": "founded_year", "value": node.text(), "source": "site"})
        return page
```

Facts from a plugin's `extract` should use source `site`. They become method `site` in Silver and take precedence over
every other method in Gold. `sites/marham.py` is a working example that seeds
the about and contact pages directly.

## Configuration

| Variable | Default | |
|---|---|---|
| `REDIS_URL` | `redis://localhost:6379/0` | job queues, rate limits, proxy health, robots cache |
| `DP_SECRET_PROXIES_FILE` | `~/.config/xentolab/proxies.json` | provider JSON (`{"proxies": [{host, port, username, password}]}`); keep it outside git (`chmod 600`). Missing = direct connections |
| `SCRAPE_USER_AGENT` | `XentoBot/1.0 (+https://xento.pk/bot)` | also the robots.txt agent token |
| `SCRAPE_HOST_DELAY_SECONDS` | 2 | gap after each request to a host |
| `SCRAPE_FETCH_TIMEOUT_SECONDS` / `SCRAPE_MAX_BYTES` / `SCRAPE_MAX_REDIRECTS` | 20 / 3 MB / 5 | |
| `SCRAPE_MAX_FETCH_ATTEMPTS` | 3 | for timeouts, connection errors, proxy errors, 5xx, 429 |
| `SCRAPE_MAX_PAGES_PER_DOMAIN` / `SCRAPE_MAX_DEPTH` | 4 / 1 | site plugins may override |
| `SCRAPE_MAX_DELIVERIES` / `SCRAPE_CLAIM_IDLE_SECONDS` | 5 / 120 | job redelivery and dead-lettering |
| `SCRAPE_BLOB_DIR` / `SCRAPE_BLOB_TTL_HOURS` | `data/scrape/blobs` / 48 | |
| `OLLAMA_URL` / `SCRAPE_LLM_MODEL` / `SCRAPE_LLM_TEXT_BUDGET` | `http://localhost:11434` / `Qwen3.5:4B` / 9000 chars | |

Proxies: each domain sticks to one proxy for its crawl, and each retry moves to the next. A proxy that fails 5 times
in a row cools down for 10 minutes. A `502` on CONNECT counts against the site, not the proxy. Proxies never relax
the per-host limits or `robots.txt`.
