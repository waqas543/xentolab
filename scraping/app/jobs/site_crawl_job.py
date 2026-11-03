"""Generic crawl job: start from `seed_urls`, follow links under the `crawl:` policy and run the configured
parsers on every page. Contacts and metadata go to their usual files; any other parser's output lands in
`<prefix>_pages_*.jsonl` under `data.<parser name>`.

Re-running skips writing records for pages already written (dedup on URL), but pages are fetched again
because links are rediscovered from them.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.core.exceptions import ConfigError
from app.core.logging import get_logger
from app.core.models import (
    BaseRecord,
    ContactInfo,
    ContactRecord,
    MetadataRecord,
    PageMetadata,
    PageRecord,
    PageResponse,
    RecordStatus,
)
from app.core.utils import host_of, normalize_url
from app.crawl.crawler import Crawler, CrawlPolicy, CrawlResult
from app.jobs.base_job import BaseJob, RunContext, register_job

log = get_logger(__name__)


@register_job("site_crawl")
class SiteCrawlJob(BaseJob):
    def validate_input(self) -> dict[str, Any]:
        return {"seed_urls": self.config.seed_urls, "parsers": self.config.parsers,
                "policy": self.config.crawl.model_dump()}

    async def execute(self, ctx: RunContext, *, limit: int | None = None) -> None:
        if not self.config.seed_urls:
            raise ConfigError(f"job {self.config.job_name!r} needs seed_urls")
        policy = CrawlPolicy.from_config(self.config.crawl)
        if limit is not None:
            policy.max_pages = min(policy.max_pages, limit)
        crawler = Crawler(ctx.fetcher, policy, concurrency=self.config.rate_limit.max_concurrency)
        async for result in crawler.crawl(self.config.seed_urls):
            self._handle(ctx, result)

    def _handle(self, ctx: RunContext, result: CrawlResult) -> None:
        key = normalize_url(result.item.url)
        if result.error is not None:
            error = self.error_record(ctx, stage="crawl", error=result.error, url=result.item.url, key=key)
            self.commit(ctx, key, RecordStatus.FAILED, [error], seen=[], error=str(result.error)[:300])
            return
        assert result.page is not None
        records, seen = self._records(ctx, result, result.page)
        unseen = ctx.state.unseen(k for _, k in seen)
        keep = [record for record, (_, k) in zip(records, seen, strict=True) if k in unseen]
        self.commit(ctx, key, RecordStatus.SUCCESS, keep, seen=[s for s in seen if s[1] in unseen])

    def _records(self, ctx: RunContext, result: CrawlResult,
                 page: PageResponse) -> tuple[list[BaseRecord], list[tuple[str, str]]]:
        url = normalize_url(page.final_url)
        records: list[BaseRecord] = []
        seen: list[tuple[str, str]] = []
        data: dict[str, Any] = {}
        for name, parser in self.parsers.items():
            output = parser.parse(page)
            if isinstance(output, ContactInfo):
                if not output.is_empty():
                    key = f"contact|{url}"
                    records.append(ContactRecord(
                        **self.base_fields(ctx, "contacts", key), source_type="website", source_url=page.final_url,
                        domain=host_of(page.final_url), phone_numbers=output.phone_numbers,
                        phone_details=output.phones, emails=output.emails, locations=output.location_texts,
                        location_details=output.locations, social_links=output.social_links))
                    seen.append(("contacts", key))
            elif isinstance(output, PageMetadata):
                key = f"meta|{url}"
                records.append(MetadataRecord(
                    **self.base_fields(ctx, "metadata", key), page_url=page.url, final_url=page.final_url,
                    domain=host_of(page.final_url), page_kind="crawl", **output.model_dump(exclude={"url"}),
                    fetch=page.fetch_info()))
                seen.append(("metadata", key))
            else:
                data[name] = _jsonable(output)
        if data:
            key = f"page|{url}"
            records.append(PageRecord(
                **self.base_fields(ctx, "pages", key), page_url=page.url, final_url=page.final_url,
                domain=host_of(page.final_url), depth=result.item.depth, parent_url=result.item.parent,
                data=data, fetch=page.fetch_info()))
            seen.append(("pages", key))
        return records, seen


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value
