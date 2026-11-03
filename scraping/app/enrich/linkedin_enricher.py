"""The company's public LinkedIn page: pick the right `linkedin.com/company/<slug>` link, fetch it (no browser) and
parse it.

Candidates, best first:
1. a LinkedIn company link on the company's own website (trusted: the company put it there);
2. a LinkedIn company result from search (or a search summary's sources) whose title or slug matches the
   company name, so another company's page is not picked up.
Numeric links (`/company/40844851`) are skipped: they only show the login page. Only the best candidate is
fetched. The login wall comes and goes for the same URL, so a walled page is retried with backoff.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from app.core.config import ExtractionRules, JobConfig, LinkedInConfig
from app.core.exceptions import BlockedError
from app.core.logging import get_logger
from app.core.models import CompanyInput, ContactInfo, LinkedInCompany, PageResponse, SearchResult, SearchSummary
from app.enrich.domain_detector import name_tokens
from app.fetch.fetcher import Fetcher
from app.fetch.retry import RetryPolicy
from app.parse import ParseContext
from app.parse.contact_extractor import extract_from_text
from app.parse.linkedin_parser import LinkedInCompanyParser, company_slug, company_url

log = get_logger(__name__)
_COMPACT = re.compile(r"[^a-z0-9]")


@dataclass(frozen=True)
class LinkedInCandidate:
    slug: str
    url: str
    found_on: Literal["website", "search"]


@dataclass
class LinkedInResult:
    company: LinkedInCompany
    page: PageResponse
    found_on: Literal["website", "search"]
    name_matches: bool


def name_matches(company_name: str, *texts: str) -> bool:
    """Whether any text (a page title, a slug, a page name) names the company: its core name compacted
    (`sadaqat` in `sadaqatgroup`), or its first distinctive word plus at least half of its words."""
    _, core = name_tokens(company_name)
    compact = "".join(core)
    words = [t for t in core if len(t) >= 3]
    for text in texts:
        norm = _COMPACT.sub("", (text or "").lower())
        if not norm:
            continue
        if len(compact) >= 4 and compact in norm:
            return True
        if words and words[0] in norm and sum(w in norm for w in words) / len(words) >= 0.5:
            return True
    return False


def find_candidates(company: CompanyInput, website_links: list[str], results: list[SearchResult],
                    summaries: list[SearchSummary]) -> list[LinkedInCandidate]:
    found: dict[str, LinkedInCandidate] = {}

    def add(url: str, found_on: Literal["website", "search"]) -> None:
        slug = company_slug(url)
        if slug and not slug.isdigit() and slug not in found:
            found[slug] = LinkedInCandidate(slug=slug, url=company_url(slug), found_on=found_on)

    for url in website_links:
        add(url, "website")
    for result in results:
        slug = company_slug(result.url) if result.resolved else None
        if slug and name_matches(company.search_name, result.title.split("|")[0], slug):
            add(result.url, "search")
    for summary in summaries:
        for source in summary.sources:
            slug = company_slug(source.url or "")
            if slug and name_matches(company.search_name, (source.title or "").split("|")[0], slug):
                add(source.url or "", "search")
    return list(found.values())


class LinkedInEnricher:
    def __init__(self, config: LinkedInConfig, job: JobConfig, fetcher: Fetcher, rules: ExtractionRules) -> None:
        self.config = config
        self.fetcher = fetcher
        self.retry = RetryPolicy.from_config(config.retry or job.retry)
        self.parser = LinkedInCompanyParser(ParseContext(rules=rules, config=job))

    async def fetch(self, company: CompanyInput, candidate: LinkedInCandidate) -> LinkedInResult:
        """Fetch + parse the page; a login wall pauses LinkedIn requests and retries. Raises ScrapingError."""
        async for attempt in self.retry.retrying():
            with attempt:
                number = attempt.retry_state.attempt_number
                page = await self.fetcher.fetch(candidate.url, use_cache=False)
                try:
                    parsed = self.parser.parse(page)
                except BlockedError:
                    pause = self.retry.wait_seconds(number)
                    self.fetcher.penalize(pause)
                    log.info("linkedin.login_wall", url=candidate.url, attempt=number, pause_s=round(pause, 1))
                    raise
                parsed = parsed.model_copy(update={"recent_posts": parsed.recent_posts[:self.config.max_posts]})
                matches = name_matches(company.search_name, parsed.name)
                log.info("linkedin.fetched", company=company.company_key, url=candidate.url,
                         found_on=candidate.found_on, name=parsed.name, name_matches=matches)
                return LinkedInResult(company=parsed, page=page, found_on=candidate.found_on, name_matches=matches)
        raise AssertionError("unreachable")  # retrying() re-raises the last error


def linkedin_text(company: LinkedInCompany) -> str:
    """The page as plain text, for the company-profile model."""
    lines = [f"Name: {company.name}"]
    for label, value in (("Tagline", company.tagline), ("Industry", company.industry),
                         ("Company size", company.company_size), ("Headquarters", company.headquarters),
                         ("Type", company.organization_type), ("Founded", company.founded_year),
                         ("Website", company.website)):
        if value:
            lines.append(f"{label}: {value}")
    if company.specialties:
        lines.append("Specialties: " + ", ".join(company.specialties))
    if company.locations:
        lines.append("Locations: " + "; ".join(company.locations))
    if company.description:
        lines.append(company.description)
    return "\n".join(lines)


def linkedin_contacts(company: LinkedInCompany, rules: ExtractionRules) -> ContactInfo:
    return extract_from_text("\n".join([company.description or "", *company.locations]), rules)
