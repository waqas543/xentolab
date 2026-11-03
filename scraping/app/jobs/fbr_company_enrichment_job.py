"""FBR company enrichment: for every company in the FBR input, search the configured engines (Google, DuckDuckGo),
keep all results and summaries (Google's AI Overview), detect the official domain, crawl a few pages of it,
build a company profile + summary from them with a local LLM, and write search results, search summaries,
domains, contacts, metadata, company profiles and a per-company summary.

Unit of work = one company (keyed by its normalized name). Each company's records are written and flushed
before the company is marked done, so a restart never reprocesses finished companies; failed ones are retried.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from typing import Any

from app.core.exceptions import ScrapingError
from app.core.input_reader import InputReader, RejectedRow
from app.core.logging import get_logger
from app.core.models import (
    BaseRecord,
    CompanyInput,
    CompanyProfile,
    CompanyProfileRecord,
    ContactInfo,
    ContactRecord,
    DomainRecord,
    EnrichmentSummaryRecord,
    LinkedInCompanyRecord,
    MetadataRecord,
    RecordStatus,
    SearchResultRecord,
    SearchSummaryRecord,
    SkippedRecord,
)
from app.core.utils import host_of, normalize_url, registrable_domain, unique
from app.crawl.scheduler import run_bounded
from app.enrich.company_enricher import CompanyEnricher, CompanyEnrichment
from app.enrich.linkedin_enricher import linkedin_contacts
from app.jobs.base_job import BaseJob, RunContext, register_job
from app.parse.linkedin_parser import company_slug

log = get_logger(__name__)
SAMPLE_SIZE = 5


@register_job("fbr_company_enrichment")
class FbrCompanyEnrichmentJob(BaseJob):
    # ------------------------------------------------------------ input

    def validate_input(self) -> dict[str, Any]:
        reader = InputReader(self.config, self.rules)
        reasons: Counter[str] = Counter()
        valid, samples, rejected = 0, [], []
        for item in reader:
            if isinstance(item, RejectedRow):
                reasons[item.reason] += 1
                if len(rejected) < SAMPLE_SIZE:
                    rejected.append({"row": item.row_number, "reason": item.reason, "details": item.details})
                continue
            valid += 1
            if len(samples) < SAMPLE_SIZE:
                samples.append({"row": item.row_number, "resolved_name": item.resolved_name,
                                "search_name": item.search_name, "city": item.city, "ntn": item.ntn})
        return {"input_file": str(self.config.input_file), "rows": valid + sum(reasons.values()), "valid": valid,
                "rejected": dict(reasons), "samples": samples, "rejected_samples": rejected}

    def _work(self, ctx: RunContext, reader: InputReader, limit: int | None) -> Iterator[CompanyInput]:
        done = ctx.state.done_keys()
        queued = 0
        for item in reader:
            if isinstance(item, RejectedRow):
                if f"row:{item.row_number}" not in done:
                    self._skip(ctx, item)
                continue
            if item.company_key in done:
                ctx.stats.items["already_done"] += 1
                continue
            if limit is not None and queued >= limit:
                return
            queued += 1
            yield item

    def _skip(self, ctx: RunContext, row: RejectedRow) -> None:
        key = f"row:{row.row_number}"
        record = SkippedRecord(**self.base_fields(ctx, "skipped", key), row_number=row.row_number, reason=row.reason,
                               details=row.details, raw=row.raw)
        self.commit(ctx, key, RecordStatus.SKIPPED, [record], seen=[("skipped", key)], row_number=row.row_number)
        log.info("row.skipped", row=row.row_number, reason=row.reason)

    # ------------------------------------------------------------ processing

    async def execute(self, ctx: RunContext, *, limit: int | None = None) -> None:
        reader = InputReader(self.config, self.rules)
        linkedin_fetcher = self.fetcher_for(ctx, self.config.with_overrides(self.config.linkedin))
        engine_fetchers = {engine.name: self.fetcher_for(ctx, self.config.with_overrides(engine))
                           for engine in self.config.search.engines}
        enricher = CompanyEnricher(self.config, ctx.fetcher, self.parsers, self.rules,
                                   engine_fetchers=engine_fetchers, user_agent=self.settings.user_agent,
                                   ollama_url=self.settings.ollama_url,
                                   linkedin_fetcher=linkedin_fetcher)

        async def process(company: CompanyInput) -> None:
            await self._process(ctx, enricher, company)

        try:
            await run_bounded(self._work(ctx, reader, limit), process, self.config.rate_limit.max_concurrency,
                              ctx.stop)
        finally:
            await enricher.aclose()

    async def _process(self, ctx: RunContext, enricher: CompanyEnricher, company: CompanyInput) -> None:
        identity = self._identity(company)
        try:
            enrichment = await enricher.enrich(company)
        except ScrapingError as exc:
            error = self.error_record(ctx, stage=getattr(exc, "stage", "search"), error=exc,
                                      url=getattr(exc, "url", None),
                                      key=company.company_key, row_number=company.row_number, **identity)
            self.commit(ctx, company.company_key, RecordStatus.FAILED, [error], seen=[],
                        row_number=company.row_number, error=f"{type(exc).__name__}: {str(exc)[:300]}")
            log.warning("company.failed", company=company.company_key, row=company.row_number, error=str(exc))
            return

        records, seen = self._records(ctx, enrichment)
        unseen = ctx.state.unseen(key for _, key in seen)
        keep = [record for record, (_, key) in zip(records, seen, strict=True) if key in unseen]
        for warning in [*enrichment.search_errors, *enrichment.warnings]:
            keep.append(self.error_record(ctx, stage=warning.stage, error=RuntimeError(warning.message),
                                          error_type=warning.error_type, url=warning.url, attempts=warning.attempts,
                                          key=company.company_key, row_number=company.row_number,
                                          engine=warning.engine, **identity))
        try:
            self.commit(ctx, company.company_key, enrichment.status, keep,
                        seen=[(t, k) for t, k in seen if k in unseen], row_number=company.row_number)
        except ScrapingError as exc:  # a record failed validation: a bug, but don't stop the run
            error = self.error_record(ctx, stage="write", error=exc, key=company.company_key,
                                      row_number=company.row_number, **identity)
            self.commit(ctx, company.company_key, RecordStatus.FAILED, [error], seen=[],
                        row_number=company.row_number, error=f"{type(exc).__name__}: {str(exc)[:300]}")
            return
        likely = enrichment.likely
        log.info("company.done", company=company.company_key, row=company.row_number, status=str(enrichment.status),
                 engines=enrichment.engines, results=len(enrichment.results),
                 summaries=len(enrichment.summaries), likely_domain=likely.domain if likely else None,
                 profile=enrichment.profile is not None, linkedin=enrichment.linkedin is not None,
                 pages=enrichment.pages_fetched, ms=enrichment.duration_ms)

    # ------------------------------------------------------------ records

    @staticmethod
    def _identity(company: CompanyInput) -> dict[str, Any]:
        return {"company_key": company.company_key, "company_name": company.company_name,
                "business_name": company.business_name}

    def _records(self, ctx: RunContext, e: CompanyEnrichment) -> tuple[list[BaseRecord], list[tuple[str, str]]]:
        """All records for one company, paired with (record_type, dedup_key)."""
        company = e.company
        ident = {**self._identity(company), "search_query": e.query}
        likely = e.likely
        by_domain = {c.domain: c for c in e.candidates}
        records: list[BaseRecord] = []
        seen: list[tuple[str, str]] = []

        def add(record_type: str, key: str, build: Any, **extra: Any) -> None:
            records.append(build(self.base_fields(ctx, record_type, key, **ident, **extra)))
            seen.append((record_type, key))

        for result in e.results:
            domain = registrable_domain(result.url)
            candidate = by_domain.get(domain)
            contacts = e.snippet_contacts.get(result.url, ContactInfo())
            url_key = normalize_url(result.url) if result.resolved else result.redirect_url or result.url
            key = f"{company.company_key}|{result.engine}|{url_key}"
            add("search_results", key, lambda base, r=result, c=contacts, d=domain, cand=candidate, k=key:
                SearchResultRecord(
                    **base, dedup_key=k, engine=r.engine, rank=r.rank, result_title=r.title, result_url=r.url,
                    domain=r.domain, likely_domain=bool(likely and d == likely.domain),
                    phone_numbers=c.phone_numbers, emails=c.emails, locations=c.location_texts,
                    social_links=c.social_links, raw_snippet=r.snippet, status=RecordStatus.SUCCESS,
                    metadata={"engine": r.engine, "display_url": r.display_url, "site_name": r.site_name,
                              "registrable_domain": d, "url_resolved": r.resolved, "redirect_url": r.redirect_url,
                              "domain_category": cand.category if cand else None,
                              "domain_score": cand.score if cand else None}),
                loader=e.loaders.get(result.engine) or ctx.fetcher.loader_name)

        for index, summary in enumerate(e.summaries):
            contacts = e.summary_contacts.get(index, ContactInfo())
            add("search_summaries", f"search_summary|{company.company_key}|{summary.engine}|{summary.kind}",
                lambda base, s=summary, c=contacts: SearchSummaryRecord(
                    **base, engine=s.engine, summary_type=s.kind, text=s.text, sources=s.sources,
                    source_domains=unique(d for src in s.sources
                                          if (d := registrable_domain(src.url or src.site or ""))),
                    page_url=s.page_url, phone_numbers=c.phone_numbers, emails=c.emails,
                    locations=c.location_texts, social_links=c.social_links),
                loader=e.loaders.get(summary.engine) or ctx.fetcher.loader_name)
            if not contacts.is_empty():
                add("contacts", f"contact|{company.company_key}|summary|{summary.engine}|{summary.kind}",
                    lambda base, s=summary, c=contacts: self._contact(base, "search_summary", s.page_url, c),
                    loader=e.loaders.get(summary.engine) or ctx.fetcher.loader_name)

        for cand in e.candidates:
            add("domains", f"domain|{company.company_key}|{cand.domain}", lambda base, c=cand: DomainRecord(
                **base, domain=c.domain, hosts=c.hosts, category=c.category, score=c.score,
                likely_domain=c.likely_domain, reasons=c.reasons, result_urls=c.result_urls, best_rank=c.best_rank))

        for url, contacts in e.snippet_contacts.items():
            add("contacts", f"contact|{company.company_key}|search|{normalize_url(url)}",
                lambda base, u=url, c=contacts: self._contact(base, "search_snippet", u, c))
        for page in e.website_pages:
            url = normalize_url(page.page.final_url)
            if not page.contacts.is_empty():
                add("contacts", f"contact|{company.company_key}|website|{url}",
                    lambda base, p=page: self._contact(base, "website", p.page.final_url, p.contacts))
            if page.metadata is not None:
                add("metadata", f"meta|{company.company_key}|{url}", lambda base, p=page: MetadataRecord(
                    **base, page_url=p.page.url, final_url=p.page.final_url, domain=host_of(p.page.final_url),
                    page_kind="website", **p.metadata.model_dump(exclude={"url"}), fetch=p.page.fetch_info()))

        merged = e.all_contacts()
        if e.profile is not None and likely is not None:
            profile = e.profile
            shared = set(CompanyProfile.model_fields) & set(CompanyProfileRecord.model_fields)
            fields = profile.model_dump(include=shared - {"domain", "model", "company_name"})
            add("profiles", f"profile|{company.company_key}|{likely.domain}",
                lambda base, p=profile, f=fields: CompanyProfileRecord(
                    **base, **f, row_number=company.row_number, domain=p.domain, model=p.model,
                    profile_company_name=p.company_name, phone_numbers=merged.phone_numbers, emails=merged.emails,
                    social_links=merged.social_links, source_urls=e.profile_sources, text_chars=e.profile_text_chars))

        if e.linkedin is not None:
            li = e.linkedin
            li_contacts = linkedin_contacts(li.company, self.rules)
            li_fields = li.company.model_dump(exclude={"url", "name"})
            website_domain = registrable_domain(li.company.website) if li.company.website else None
            slug = company_slug(li.company.url) or li.company.url
            add("linkedin_companies", f"linkedin|{company.company_key}|{slug}",
                lambda base, r=li, f=li_fields, c=li_contacts, w=website_domain: LinkedInCompanyRecord(
                    **base, **f, row_number=company.row_number, linkedin_url=r.company.url, found_on=r.found_on,
                    name_matches=r.name_matches, linkedin_name=r.company.name,
                    website_matches_likely_domain=(w == likely.domain) if w and likely else None,
                    phone_numbers=c.phone_numbers, emails=c.emails, fetch=r.page.fetch_info()),
                loader=e.linkedin.page.loader)

        add("enrichment_summary", f"summary|{company.company_key}", lambda base: EnrichmentSummaryRecord(
            **base, status=e.status, row_number=company.row_number, ntn=company.ntn,
            registration_no=company.registration_no, city=company.city, province=company.province,
            search_engines=e.engines, summary_types=unique(f"{s.engine}:{s.kind}" for s in e.summaries),
            results_count=len(e.results),
            candidate_domains=[c.domain for c in e.candidates if c.category == "candidate"],
            likely_domain=likely.domain if likely else None, likely_domain_score=likely.score if likely else None,
            pages_fetched=e.pages_fetched, summary=e.profile.summary if e.profile else None,
            industry=e.profile.industry if e.profile else None,
            phone_numbers=merged.phone_numbers, emails=merged.emails,
            locations=merged.location_texts, social_links=merged.social_links, duration_ms=e.duration_ms,
            errors=[f"{w.stage} {w.url or ''}: {w.message}"[:500] for w in e.warnings]))
        return records, seen

    @staticmethod
    def _contact(base: dict[str, Any], source_type: str, url: str, contacts: ContactInfo) -> ContactRecord:
        return ContactRecord(**base, source_type=source_type, source_url=url, domain=host_of(url),
                             phone_numbers=contacts.phone_numbers, phone_details=contacts.phones,
                             emails=contacts.emails, locations=contacts.location_texts,
                             location_details=contacts.locations, social_links=contacts.social_links)
