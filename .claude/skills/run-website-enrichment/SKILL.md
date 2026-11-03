---
name: run-website-enrichment
description: Run, monitor and troubleshoot company-website enrichment end to end - scraping engine agents (Redis Streams), crawl campaigns, the websites.profiles pipeline into Gold, the app schema, and the CSV export for review. Use when asked to enrich/crawl/scrape domains, refresh website profiles, check crawl progress, investigate failed fetches, dead letters, proxies, or why a profile field is missing or wrong.
---

# Run website enrichment

```
scraping engine (collects)                  websites.profiles pipeline (ingests, like any source)
seed -> frontier/fetcher/parser/enricher/writer -> web.facts -> Bronze -> silver.domain_facts -> GOLD_BUILD
                                                                  -> gold.domain_profiles -> app.websites -> mobile
```
Docs: `docs/data-platform/web-enrichment.md`. Commands run from the repo root unless they start with `cd backend`.

## Preflight
```bash
redis-cli ping                               # PONG; else: brew services start redis
curl -s localhost:11434/api/version          # Ollama, for the LLM step; model: ollama list | grep -i qwen3.5
make scrape-proxy-check                      # proxies from ~/.config/xentolab/proxies.json (secret; never print it)
df -h /                                      # Gold rebuild writes ~0.5 GB for PK; stop if < 1.5 GB free and ask
```

## Run
```bash
make scrape-up                               # every agent in one process (dev). Production: one process per agent,
                                             # make scrape-agent AGENT=fetcher CONCURRENCY=64 (repeat to scale)
make scrape-seed LIMIT=10                    # top N business-candidate domains for COUNTRY (default PK)
make scrape-seed DOMAINS="a.pk b.pk"         # specific domains
make scrape-status                           # queues, in progress, retries, dead letters, proxies, blobs, campaign
```
- When a campaign completes, the frontier queues `websites.profiles`, and a data-platform **worker** must be running to execute it (`make up` or `make data-platform-worker`).
- To ingest now in the foreground, run `make pipeline-websites`.
- Then run `make app-schema` so the mobile app serves the new profiles.
- Review with `make scrape-export`, which writes `data/exports/enrichment/domain_profiles.csv`.

Throughput:
- Fetching is limited by politeness (1 request per host, 2s gap), so expect ~20-30 min per 20k pages.
- The LLM step (Qwen3.5:4B) takes ~2-10s per domain and is the bottleneck. Use `seed --no-enrich` to skip it.

## Troubleshoot

| Symptom | Look at | Usual fix |
|---|---|---|
| Pages `failed` | `make scrape-status` (errors by class); `psql -c "select url,error_class,error_message from web.crawl_urls where state='failed' order by updated_at desc limit 20"` | `proxy`: run `make scrape-proxy-check`. `robots_denied`: respect it. `http_4xx`/`geo_block`: the site blocks bots or foreign IPs, so consider a site plugin. `tls` already falls back to unverified. |
| Domain stuck `enriching` | `redis-cli xlen enrich:requests`; is Ollama up? | Start Ollama. Lost jobs are re-published once the pipeline is idle for 2 minutes. |
| Dead letters | `cd backend && ../.venv/bin/python -m data_platform.scrape dlq` | Fix the cause, then `dlq --requeue` |
| Profile field empty | `select field,value,source,evidence from web.facts where domain='x'` and `select * from gold.domain_profiles where domain='x'` (`field_sources`) | No fact: the site doesn't state it, so add a site plugin (skill `add-site-plugin`) or a fact source (skill `add-domain-fact-source`). Fact exists but lost: see precedence in `gold/domain_profiles.py`. |
| Profile not in the app | Did `websites.profiles` complete (`python -m data_platform runs`)? Was `make app-schema` run? | Run the missing step |
| Wrong city | Gazetteer names: `select name from gold.cities where name ilike '%x%'` | City comes from the most populous place mentioned in the address facts |
| Disk filling | `make scrape-status` (blobs), `df -h /` | Blobs are deleted after writing. `python -m data_platform.scrape sweep --hours 1` removes leftovers. The fetcher pauses below `DATA_PLATFORM_MIN_FREE_DISK_MB`. |

Stopping: Ctrl+C, or SIGINT/SIGTERM to the agent process. In-flight jobs finish or are reclaimed by another worker. Nothing is lost: Postgres holds crawl state, and Redis holds only outstanding jobs.

## Don'ts
- Don't write `gold.domain_profiles` directly; only the Gold build does.
- Don't print or commit the proxy credentials.
- Don't lower `SCRAPE_HOST_DELAY_SECONDS` or bypass robots.txt to speed things up.
- Don't run a real Gold rebuild on low disk without asking the user.
