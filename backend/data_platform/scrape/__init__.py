"""Website enrichment engine: stateless agents connected by Redis Streams.

    frontier ─► scrape:requests ─► fetcher ─► scrape:fetched ─► parser ─► scrape:parsed ─► writer ─► Postgres
        ▲                                                                                     │
        └────────────── web.crawl_urls (discovered links, domain completion) ◄───────────────┘
    frontier ─► enrich:requests ─► enricher (Ollama) ─► enrich:results ─► writer ─► gold.domain_profiles

Only the frontier holds crawl state, and it keeps it in Postgres (web.crawl_*). Fetcher, parser,
enricher and writer are stateless: everything they need travels in the message, in Redis (rate
limits, proxy health, robots.txt cache) or in the blob store (raw HTML until it is parsed and written).
Run each agent with `python -m data_platform.scrape <agent>`; start more copies to scale a stage.
"""
