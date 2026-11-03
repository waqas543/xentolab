"""Pydantic models for every stage boundary.

Pipeline models (PageResponse, SearchResult, ContactInfo, ...) move between stages.
Output records (subclasses of BaseRecord) are what writers persist: one record type per output file.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.utils import is_http_url, unique, utcnow


class RecordStatus(StrEnum):
    SUCCESS = "success"
    NO_RESULTS = "no_results"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED = "skipped"


# ---------------------------------------------------------------- pipeline models


class PageResponse(BaseModel):
    """What every loader returns: raw page content plus request metadata."""

    url: str
    final_url: str
    status_code: int
    html: str
    content_type: str | None = None
    latency_ms: int
    attempts: int = 1
    loader: str
    render_js: bool = False
    fetched_at: datetime = Field(default_factory=utcnow)

    @property
    def size_bytes(self) -> int:
        return len(self.html.encode("utf-8"))

    def fetch_info(self) -> dict[str, Any]:
        return {"loader": self.loader, "status_code": self.status_code, "latency_ms": self.latency_ms,
                "attempts": self.attempts, "size_bytes": self.size_bytes, "render_js": self.render_js,
                "fetched_at": self.fetched_at.isoformat()}


class CompanyInput(BaseModel):
    """One input row after field mapping and name resolution."""

    row_number: int
    company_key: str = Field(description="Normalized name; the resume and de-duplication key")
    company_name: str | None = None
    business_name: str | None = None
    resolved_name: str = Field(description="The name actually used (company_name, else business_name)")
    search_name: str = Field(description="resolved_name with legal suffixes and noise removed")
    ntn: str | None = None
    registration_no: str | None = None
    province: str | None = None
    city: str | None = None
    address: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class SearchResult(BaseModel):
    rank: int
    title: str
    url: str = Field(description="The result's URL; while `resolved` is false, the best guess (e.g. breadcrumb)")
    domain: str
    display_url: str | None = None
    snippet: str | None = None
    engine: str = "duckduckgo"
    site_name: str | None = None
    redirect_url: str | None = Field(default=None, description="Engine redirect link the URL was resolved from")
    resolved: bool = True


class SummarySource(BaseModel):
    """A page a search summary cites."""

    title: str | None = None
    url: str | None = None
    site: str | None = Field(default=None, description="Origin, e.g. https://digikhata.pk")
    site_name: str | None = None
    snippet: str | None = None
    redirect_url: str | None = None


class SearchSummary(BaseModel):
    """A summary block on a result page, e.g. Google's AI Overview."""

    engine: str
    kind: str = Field(description="ai_overview")
    text: str
    sources: list[SummarySource] = Field(default_factory=list)
    page_url: str


class KeyPerson(BaseModel):
    name: str
    role: str | None = None


class CompanyProfile(BaseModel):
    """What the model extracted from a company's website, after the evidence check."""

    name: str = Field(description="The input company name the profile was asked for")
    domain: str
    model: str
    prompt_version: str
    company_name: str | None = None
    summary: str | None = None
    industry: str | None = None
    business_type: str | None = None
    products_services: list[str] = Field(default_factory=list)
    brands: list[str] = Field(default_factory=list)
    key_people: list[KeyPerson] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    export_markets: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    partners_clients: list[str] = Field(default_factory=list)
    city: str | None = None
    address: str | None = None
    founded_year: int | None = None
    employees: str | None = None
    legal_form: str | None = None
    parent_company: str | None = None
    stock_listing: str | None = None
    financial_info: str | None = None
    production_capacity: str | None = None
    evidence: dict[str, str] = Field(default_factory=dict, description="field -> exact quote from the text")
    dropped: list[str] = Field(default_factory=list, description="field:value the evidence check rejected")
    llm_ms: int = 0


class LinkedInPost(BaseModel):
    text: str
    url: str | None = None
    published_at: str | None = None
    likes: int | None = None


class LinkedInCompany(BaseModel):
    """The public (logged-out) LinkedIn company page."""

    url: str
    name: str
    tagline: str | None = None
    description: str | None = None
    industry: str | None = None
    website: str | None = None
    company_size: str | None = None
    headquarters: str | None = None
    organization_type: str | None = None
    founded_year: int | None = None
    specialties: list[str] = Field(default_factory=list)
    followers: int | None = None
    employees_on_linkedin: int | None = None
    locations: list[str] = Field(default_factory=list)
    logo_url: str | None = None
    recent_posts: list[LinkedInPost] = Field(default_factory=list)


class SearchPage(BaseModel):
    """Everything parsed from one search result page."""

    results: list[SearchResult] = Field(default_factory=list)
    summaries: list[SearchSummary] = Field(default_factory=list)


class PhoneNumber(BaseModel):
    number: str = Field(description="E.164 when the number is dialable as such, else national digits")
    raw: str
    kind: Literal["mobile", "landline", "uan", "toll_free", "other"] = "other"


class Location(BaseModel):
    text: str
    kind: Literal["city", "province", "address"]
    city: str | None = None
    province: str | None = None


class ContactInfo(BaseModel):
    phones: list[PhoneNumber] = Field(default_factory=list)
    emails: list[str] = Field(default_factory=list)
    locations: list[Location] = Field(default_factory=list)
    social_links: dict[str, list[str]] = Field(default_factory=dict)

    @property
    def phone_numbers(self) -> list[str]:
        return unique(phone.number for phone in self.phones)

    @property
    def location_texts(self) -> list[str]:
        return unique(location.text for location in self.locations)

    def is_empty(self) -> bool:
        return not (self.phones or self.emails or self.locations or self.social_links)

    def merge(self, other: ContactInfo) -> ContactInfo:
        phones = {phone.number: phone for phone in [*self.phones, *other.phones]}
        locations = {(loc.kind, loc.text.lower()): loc for loc in [*self.locations, *other.locations]}
        social: dict[str, list[str]] = {}
        for links in (self.social_links, other.social_links):
            for platform, urls in links.items():
                social[platform] = unique([*social.get(platform, []), *urls])
        return ContactInfo(phones=list(phones.values()), emails=unique([*self.emails, *other.emails]),
                           locations=list(locations.values()), social_links=social)


class PageMetadata(BaseModel):
    url: str
    title: str | None = None
    description: str | None = None
    canonical_url: str | None = None
    language: str | None = None
    keywords: list[str] = Field(default_factory=list)
    site_name: str | None = None
    open_graph: dict[str, str] = Field(default_factory=dict)
    twitter: dict[str, str] = Field(default_factory=dict)
    json_ld: list[dict[str, Any]] = Field(default_factory=list)


class DomainCandidate(BaseModel):
    domain: str = Field(description="Registrable domain, e.g. engro.com.pk")
    hosts: list[str] = Field(default_factory=list)
    category: str = Field(default="candidate", description="candidate, or the generic category it belongs to")
    score: float = 0.0
    likely_domain: bool = False
    reasons: list[str] = Field(default_factory=list)
    result_urls: list[str] = Field(default_factory=list)
    best_rank: int = 0


# ---------------------------------------------------------------- output records


class BaseRecord(BaseModel):
    """Fields shared by every output record. `record_type` routes a record to its output file."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    record_type: ClassVar[str]

    record_id: str
    source: str
    company_key: str | None = None
    company_name: str | None = None
    business_name: str | None = None
    search_query: str | None = None
    scraped_at: datetime = Field(default_factory=utcnow)
    job_id: str
    job_name: str
    loader: str
    status: RecordStatus = RecordStatus.SUCCESS


class SearchResultRecord(BaseRecord):
    record_type: ClassVar[str] = "search_results"

    dedup_key: str
    engine: str = "duckduckgo"
    rank: int
    result_title: str
    result_url: str
    domain: str
    likely_domain: bool = False
    phone_numbers: list[str] = Field(default_factory=list)
    emails: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    social_links: dict[str, list[str]] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    raw_snippet: str | None = None

    @field_validator("result_url")
    @classmethod
    def _http_url(cls, value: str) -> str:
        if not is_http_url(value):
            raise ValueError(f"not an http(s) URL: {value!r}")
        return value


class SearchSummaryRecord(BaseRecord):
    """One summary block from a search result page (e.g. Google's AI Overview) with the sources it cites."""

    record_type: ClassVar[str] = "search_summaries"

    engine: str
    summary_type: str
    text: str
    sources: list[SummarySource] = Field(default_factory=list)
    source_domains: list[str] = Field(default_factory=list)
    page_url: str
    phone_numbers: list[str] = Field(default_factory=list)
    emails: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    social_links: dict[str, list[str]] = Field(default_factory=dict)


class CompanyProfileRecord(BaseRecord):
    """A company's profile and summary from its website (local LLM), with contacts found by the parsers."""

    record_type: ClassVar[str] = "profiles"

    row_number: int
    domain: str
    model: str
    prompt_version: str
    profile_company_name: str | None = None
    summary: str | None = None
    industry: str | None = None
    business_type: str | None = None
    products_services: list[str] = Field(default_factory=list)
    brands: list[str] = Field(default_factory=list)
    key_people: list[KeyPerson] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    export_markets: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    partners_clients: list[str] = Field(default_factory=list)
    city: str | None = None
    address: str | None = None
    founded_year: int | None = None
    employees: str | None = None
    legal_form: str | None = None
    parent_company: str | None = None
    stock_listing: str | None = None
    financial_info: str | None = None
    production_capacity: str | None = None
    phone_numbers: list[str] = Field(default_factory=list)
    emails: list[str] = Field(default_factory=list)
    social_links: dict[str, list[str]] = Field(default_factory=dict)
    evidence: dict[str, str] = Field(default_factory=dict)
    dropped: list[str] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list, description="Website pages the model read")
    text_chars: int = 0
    llm_ms: int = 0


class LinkedInCompanyRecord(BaseRecord):
    """A company's public LinkedIn page. Written to its own folder (output_subdirs: linkedin)."""

    record_type: ClassVar[str] = "linkedin_companies"

    row_number: int
    linkedin_url: str
    found_on: Literal["website", "search"] = Field(description="Where the link came from")
    name_matches: bool = Field(description="The page's name matches the company name")
    website_matches_likely_domain: bool | None = None
    linkedin_name: str
    tagline: str | None = None
    description: str | None = None
    industry: str | None = None
    website: str | None = None
    company_size: str | None = None
    headquarters: str | None = None
    organization_type: str | None = None
    founded_year: int | None = None
    specialties: list[str] = Field(default_factory=list)
    followers: int | None = None
    employees_on_linkedin: int | None = None
    locations: list[str] = Field(default_factory=list)
    logo_url: str | None = None
    recent_posts: list[LinkedInPost] = Field(default_factory=list)
    phone_numbers: list[str] = Field(default_factory=list)
    emails: list[str] = Field(default_factory=list)
    fetch: dict[str, Any] = Field(default_factory=dict)


class ContactRecord(BaseRecord):
    record_type: ClassVar[str] = "contacts"

    source_type: Literal["search_snippet", "search_summary", "website"]
    source_url: str
    domain: str | None = None
    phone_numbers: list[str] = Field(default_factory=list)
    phone_details: list[PhoneNumber] = Field(default_factory=list)
    emails: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    location_details: list[Location] = Field(default_factory=list)
    social_links: dict[str, list[str]] = Field(default_factory=dict)


class DomainRecord(BaseRecord):
    record_type: ClassVar[str] = "domains"

    domain: str
    hosts: list[str] = Field(default_factory=list)
    category: str
    score: float
    likely_domain: bool
    reasons: list[str] = Field(default_factory=list)
    result_urls: list[str] = Field(default_factory=list)
    best_rank: int


class MetadataRecord(BaseRecord):
    record_type: ClassVar[str] = "metadata"

    page_url: str
    final_url: str
    domain: str
    page_kind: str = Field(description="search_results | website | crawl")
    title: str | None = None
    description: str | None = None
    canonical_url: str | None = None
    language: str | None = None
    keywords: list[str] = Field(default_factory=list)
    site_name: str | None = None
    open_graph: dict[str, str] = Field(default_factory=dict)
    twitter: dict[str, str] = Field(default_factory=dict)
    json_ld: list[dict[str, Any]] = Field(default_factory=list)
    fetch: dict[str, Any] = Field(default_factory=dict)


class PageRecord(BaseRecord):
    """Generic crawl output: one page plus whatever each configured parser returned, keyed by parser name."""

    record_type: ClassVar[str] = "pages"

    page_url: str
    final_url: str
    domain: str
    depth: int
    parent_url: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    fetch: dict[str, Any] = Field(default_factory=dict)


class EnrichmentSummaryRecord(BaseRecord):
    record_type: ClassVar[str] = "enrichment_summary"

    row_number: int
    ntn: str | None = None
    registration_no: str | None = None
    city: str | None = None
    province: str | None = None
    search_engines: list[str] = Field(default_factory=list, description="Engines whose results were used")
    summary_types: list[str] = Field(default_factory=list, description="e.g. google:ai_overview")
    results_count: int = 0
    candidate_domains: list[str] = Field(default_factory=list)
    likely_domain: str | None = None
    likely_domain_score: float | None = None
    pages_fetched: int = 0
    summary: str | None = Field(default=None, description="Company summary from its website (local LLM)")
    industry: str | None = None
    phone_numbers: list[str] = Field(default_factory=list)
    emails: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    social_links: dict[str, list[str]] = Field(default_factory=dict)
    duration_ms: int = 0
    errors: list[str] = Field(default_factory=list)


class ErrorRecord(BaseRecord):
    record_type: ClassVar[str] = "errors"

    status: RecordStatus = RecordStatus.FAILED
    stage: str
    error_type: str
    message: str
    url: str | None = None
    attempts: int | None = None
    row_number: int | None = None
    engine: str | None = None


class SkippedRecord(BaseRecord):
    record_type: ClassVar[str] = "skipped"

    status: RecordStatus = RecordStatus.SKIPPED
    row_number: int
    reason: str
    details: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)
