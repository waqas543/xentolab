"""Per-company enrichment: search (one or more engines) -> snippet + summary contacts -> domain detection ->
crawl of the likely website (contacts + metadata per page) -> the public LinkedIn company page -> company
profile + summary from the website and LinkedIn text by a local LLM. Returns plain results; turning them into
output records is the job's concern.

The company fails (and is retried on the next run) only when no search engine answered; an engine that failed
while another answered is reported in `search_errors`. Website failures are recorded as warnings and the
company finishes as `partial`. An unreachable LLM fails the company (retried on the next run); an unusable
answer is a warning.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from app.core.config import ExtractionRules, JobConfig
from app.core.exceptions import ScrapingError
from app.core.logging import get_logger
from app.core.models import (
    CompanyInput,
    CompanyProfile,
    ContactInfo,
    DomainCandidate,
    PageMetadata,
    PageResponse,
    RecordStatus,
    SearchResult,
    SearchSummary,
)
from app.core.utils import normalize_whitespace, registrable_domain
from app.crawl.crawler import Crawler, CrawlPolicy
from app.enrich.company_summarizer import CompanySummarizer, build_context, page_kind
from app.enrich.domain_detector import DomainDetector
from app.enrich.linkedin_enricher import LinkedInEnricher, LinkedInResult, find_candidates, linkedin_text
from app.enrich.search_engines import LinkResolver
from app.enrich.search_enricher import SearchEnricher
from app.fetch.fetcher import Fetcher
from app.parse.base_parser import BaseParser
from app.parse.contact_extractor import ContactExtractor, extract_from_text
from app.parse.html_parser import HtmlDocument
from app.parse.metadata_extractor import MetadataExtractor

log = get_logger(__name__)


@dataclass
class StageError:
    stage: str
    url: str | None
    error_type: str
    message: str
    attempts: int | None = None
    engine: str | None = None


@dataclass
class WebsitePage:
    page: PageResponse
    contacts: ContactInfo
    metadata: PageMetadata | None
    title: str | None = None
    text: str = ""


@dataclass
class CompanyEnrichment:
    company: CompanyInput
    query: str
    results: list[SearchResult] = field(default_factory=list)
    summaries: list[SearchSummary] = field(default_factory=list)
    engines: list[str] = field(default_factory=list)
    loaders: dict[str, str] = field(default_factory=dict)  # engine -> loader name
    snippet_contacts: dict[str, ContactInfo] = field(default_factory=dict)  # result URL -> contacts
    summary_contacts: dict[int, ContactInfo] = field(default_factory=dict)  # index in summaries -> contacts
    candidates: list[DomainCandidate] = field(default_factory=list)
    website_pages: list[WebsitePage] = field(default_factory=list)
    linkedin: LinkedInResult | None = None
    profile: CompanyProfile | None = None
    profile_sources: list[str] = field(default_factory=list)
    profile_text_chars: int = 0
    pages_fetched: int = 0
    warnings: list[StageError] = field(default_factory=list)
    search_errors: list[StageError] = field(default_factory=list)
    duration_ms: int = 0

    @property
    def likely(self) -> DomainCandidate | None:
        return next((c for c in self.candidates if c.likely_domain), None)

    @property
    def status(self) -> RecordStatus:
        if not self.results and not self.summaries:
            return RecordStatus.NO_RESULTS
        return RecordStatus.PARTIAL if self.warnings else RecordStatus.SUCCESS

    def all_contacts(self) -> ContactInfo:
        merged = ContactInfo()
        for contacts in [*self.snippet_contacts.values(), *self.summary_contacts.values(),
                         *(p.contacts for p in self.website_pages)]:
            merged = merged.merge(contacts)
        return merged


class CompanyEnricher:
    def __init__(self, config: JobConfig, fetcher: Fetcher, parsers: dict[str, BaseParser], rules: ExtractionRules,
                 *, engine_fetchers: dict[str, Fetcher] | None = None, user_agent: str | None = None,
                 resolver: LinkResolver | None = None, ollama_url: str | None = None,
                 linkedin_fetcher: Fetcher | None = None):
        self.config = config
        self.fetcher = fetcher
        self.rules = rules
        self.search = SearchEnricher(config, fetcher, rules, engine_fetchers=engine_fetchers, user_agent=user_agent,
                                     resolver=resolver)
        self.contacts = parsers.get("contact_extractor")
        self.metadata = parsers.get("metadata_extractor")
        self.domains = DomainDetector(rules)
        self.linkedin = (LinkedInEnricher(config.linkedin, config, linkedin_fetcher or fetcher, rules)
                         if config.linkedin.enabled else None)
        self.summarizer = (CompanySummarizer(config.summary, ollama_url or "http://localhost:11434")
                           if config.summary.enabled else None)

    async def enrich(self, company: CompanyInput) -> CompanyEnrichment:
        started = time.perf_counter()
        outcome = await self.search.search(company)
        result = CompanyEnrichment(company=company, query=outcome.query, results=outcome.results,
                                   summaries=outcome.summaries, engines=outcome.engines, loaders=outcome.loaders,
                                   pages_fetched=len(outcome.pages))
        result.search_errors = [StageError(stage="search", url=getattr(exc, "url", None), engine=engine,
                                           error_type=type(exc).__name__, message=str(exc),
                                           attempts=getattr(exc, "attempts", None))
                                for engine, exc in outcome.errors]
        if isinstance(self.contacts, ContactExtractor):
            for item in outcome.results:
                text = normalize_whitespace(f"{item.title}. {item.snippet or ''}")
                contacts = extract_from_text(text, self.rules)
                if not contacts.is_empty():
                    known = result.snippet_contacts.get(item.url)
                    result.snippet_contacts[item.url] = known.merge(contacts) if known else contacts
            for index, summary in enumerate(outcome.summaries):
                contacts = extract_from_text(summary.text, self.rules)
                if not contacts.is_empty():
                    result.summary_contacts[index] = contacts

        result.candidates = self.domains.detect(company, outcome.unique_results())
        if self.config.website.enabled and result.likely is not None:
            await self._crawl_website(result, result.likely)
        if self.linkedin is not None:
            await self._linkedin(result)
        if self.summarizer is not None and result.likely is not None:
            await self._summarize(result, result.likely)
        result.duration_ms = int((time.perf_counter() - started) * 1000)
        return result

    async def _crawl_website(self, result: CompanyEnrichment, likely: DomainCandidate) -> None:
        seeds = _website_seeds(result.results, likely.domain)
        policy = CrawlPolicy.from_config(self.config.website, allowed_domains=[likely.domain])
        crawler = Crawler(self.fetcher, policy, concurrency=min(2, self.config.rate_limit.max_concurrency))
        async for crawled in crawler.crawl(seeds):
            if crawled.error is not None:
                result.warnings.append(StageError(stage="website", url=crawled.item.url,
                                                  error_type=type(crawled.error).__name__,
                                                  message=str(crawled.error), attempts=crawled.error.attempts))
                continue
            assert crawled.page is not None
            result.pages_fetched += 1
            doc = HtmlDocument(crawled.page.html, crawled.page.final_url)
            contacts = self.contacts.extract(doc) if isinstance(self.contacts, ContactExtractor) else ContactInfo()
            metadata = self.metadata.extract(doc) if isinstance(self.metadata, MetadataExtractor) else None
            result.website_pages.append(WebsitePage(page=crawled.page, contacts=contacts, metadata=metadata,
                                                    title=doc.title, text=doc.text))
        log.info("website.crawled", company=result.company.company_key, domain=likely.domain,
                 pages=len(result.website_pages), warnings=len(result.warnings))

    async def _linkedin(self, result: CompanyEnrichment) -> None:
        assert self.linkedin is not None
        website_links = [url for page in result.website_pages for url in page.contacts.social_links.get("linkedin", [])]
        candidates = find_candidates(result.company, website_links, result.results, result.summaries)
        if not candidates:
            return
        try:
            result.linkedin = await self.linkedin.fetch(result.company, candidates[0])
        except ScrapingError as exc:
            result.warnings.append(StageError(stage="linkedin", url=candidates[0].url, error_type=type(exc).__name__,
                                              message=str(exc), attempts=getattr(exc, "attempts", None)))

    async def _summarize(self, result: CompanyEnrichment, likely: DomainCandidate) -> None:
        """Profile from the website pages plus search snippets (and any search summary such as an AI Overview)."""
        assert self.summarizer is not None
        pages = [(page_kind(p.page.final_url), p.page.final_url, p.title, p.text)
                 for p in result.website_pages if p.text.strip()]
        if result.linkedin is not None and result.linkedin.name_matches:
            linkedin = result.linkedin.company
            pages.append(("linkedin", linkedin.url, f"{linkedin.name} | LinkedIn", linkedin_text(linkedin)))
        snippets = [s.text for s in result.summaries]
        snippets += [normalize_whitespace(f"{r.title} ({r.domain}): {r.snippet}") for r in result.results
                     if r.snippet][:15]
        if not pages and not snippets:
            return
        text = build_context(pages, snippets, self.config.summary.text_budget)
        try:
            result.profile = await self.summarizer.summarize(name=result.company.resolved_name, domain=likely.domain,
                                                             text=text)
        except ValueError as exc:
            result.warnings.append(StageError(stage="summary", url=None, error_type=type(exc).__name__,
                                              message=str(exc)))
            return
        result.profile_sources = [url for _, url, _, _ in pages]
        result.profile_text_chars = len(text)

    async def aclose(self) -> None:
        await self.search.aclose()
        if self.summarizer is not None:
            await self.summarizer.aclose()


def _website_seeds(results: list[SearchResult], domain: str) -> list[str]:
    """The site's homepage (on the host search returned) first, then the matching result URLs themselves."""
    urls = [r.url for r in results if registrable_domain(r.url) == domain]
    if not urls:
        return [f"https://{domain}/"]
    first = urlsplit(urls[0])
    return [f"{first.scheme}://{first.netloc}/", *urls[:2]]
