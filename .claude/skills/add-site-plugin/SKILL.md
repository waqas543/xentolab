---
name: add-site-plugin
description: Write a custom crawler/parser for one company's website in the scraping engine (seed pages, link following, crawl budget, request headers, delay, site-specific extraction). Use when the generic crawler misses pages or facts on a particular site, a site needs gentler crawling, or the user asks for a custom scraper/parser for a company or domain.
---

# Add a site plugin

Plugins live in `backend/data_platform/scrape/sites/`. They're discovered automatically: one module, one `Site` subclass, `domains = (...)`.
- The frontier, fetcher and parser resolve the same plugin from the `site` name carried in every message, so the agents stay stateless.
- Override only what differs. Everything else is the generic engine.

Read first:
- `scrape/sites/base.py`: the contract and defaults
- `scrape/sites/marham.py`: a minimal example
- `scrape/extract.py`: what the generic parser already extracts (JSON-LD, meta, socials, contacts, about/contact links)

## Steps

1. **See what the generic engine gets for the site.**
   ```bash
   cd backend
   ../.venv/bin/python -m data_platform.scrape seed --domain example.pk --no-ingest
   # with agents running (make scrape-up), then:
   psql -d ds_companies -c "select url, state, error_class from web.crawl_urls where domain='example.pk'"
   psql -d ds_companies -c "select field, value, source from web.facts where domain='example.pk' order by field"
   ```

2. **Fetch a page by hand** to study its HTML. Use the real user agent and stay polite:
   `curl -s -A 'XentoBot/1.0 (+https://xento.pk/bot)' https://example.pk/about > /tmp/about.html`

3. **Write `scrape/sites/<name>.py`:**
   ```python
   from .base import Site

   class Example(Site):
       name = "example"                     # unique; travels in messages
       version = "1"                        # bump whenever extract() changes (recorded in parser_version)
       domains = ("example.pk",)            # registrable domains
       max_pages = 5                        # optional budget / politeness overrides
       host_delay_seconds = 5
       headers = {"Accept-Language": "en"}

       def seeds(self, domain):             # pages to fetch first: [(url, kind)]
           return [(f"https://www.{domain}/", "home"), (f"https://www.{domain}/company/profile", "company")]

       def follow(self, page_url, hrefs, domain, kind):   # [{"url", "kind", "priority"}]; [] = no discovery
           return super().follow(page_url, hrefs, domain, kind)

       def extract(self, page, tree, html, *, url, domain, country, kind):
           if kind == "company" and (node := tree.css_first(".founded")):
               page["facts"].append({"field": "founded_year", "value": node.text(strip=True), "source": "site"})
           return page
   ```
   Rules:
   - Facts use `field` names from `silver_contract.DOMAIN_FACT_FIELDS` and `"source": "site"`. In Gold, method `site` outranks every other method, so extract only what is really stated on the page.
   - `tree` has scripts and styles removed. Re-parse `html` with `selectolax.lexbor.LexborHTMLParser` if you need JSON in `<script>` tags.
   - Kinds: `home`, `about`, `contact`, `company`, `team`.
   - No network calls or state in a plugin: it runs inside stateless agents.
   - Normalize phones with `normalize_phone`, emails with `normalize_email`, social links with `normalize.social_profile`.

4. **Unit-test the plugin with a saved HTML snippet** (no network). Use `parse_html(html, url=..., domain=..., country="PK", kind=..., site=sites.get("<name>"))` in `tests/unit/test_scrape.py`, and assert the facts and links.

5. **Re-crawl and ingest.**
   - Restart the agents, because plugins load at start-up.
   - `../.venv/bin/python -m data_platform.scrape seed --domain example.pk`
   - When the campaign completes, `websites.profiles` is queued and the profile appears in `gold.domain_profiles` with `field_sources` showing `website:site`.

## Checklist
- [ ] `domains` lists registrable domains only, and no other plugin claims them (the registry raises on duplicates)
- [ ] `version` bumped if `extract` changed
- [ ] `python -m pytest tests/unit/test_scrape.py -q` passes
- [ ] Polite: `host_delay_seconds` is not lower than the default unless the site owner agreed
