"""End-to-end FBR job with a fake loader: output files, skipped/errors, resume without reprocessing."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import orjson
from conftest import fixture_html
from test_google import ACME, CAPTCHA

from app.core.config import SearchConfig, SearchEngineConfig
from app.core.exceptions import TransientLoaderError
from app.core.models import PageResponse
from app.enrich.company_summarizer import CompanySummarizer
from app.fetch.fetcher import Fetcher
from app.fetch.retry import RetryPolicy
from app.jobs import create_job
from app.loader.base import PageLoader


class FakeLoader(PageLoader):
    name = "fake"

    def __init__(self) -> None:
        super().__init__(retry=RetryPolicy(max_attempts=2, backoff_seconds=0, max_backoff_seconds=0))
        self.calls: list[str] = []

    async def _fetch_once(self, url: str, *, render_js: bool) -> PageResponse:
        self.calls.append(url)
        if "duckduckgo" in url:
            if "Broken" in url:
                raise TransientLoaderError("HTTP 503", status_code=503)
            html = fixture_html("ddg_js.html" if "Lucky" in url else "ddg_html.html")
        elif "engro.com" in url:
            html = fixture_html("company_site.html")
        else:
            html = "<html><body>other</body></html>"
        return PageResponse(url=url, final_url=url, status_code=200, html=html, latency_ms=5, loader=self.name,
                            render_js=render_js)


def read(out: Path, prefix: str) -> list[dict]:
    rows: list[dict] = []
    for path in sorted(out.glob(f"{prefix}_*.jsonl")):
        rows.extend(orjson.loads(line) for line in path.read_bytes().splitlines())
    return rows


def run(job_config, loader: FakeLoader, **kwargs):
    job = create_job(job_config, fetcher=Fetcher(loader))
    return asyncio.run(job.run(**kwargs))


def test_fbr_job_end_to_end_and_resume(job_config):
    job_config.input_file.write_text("\n".join([
        '{"company_name": "Engro Corporation Limited", "city": "Karachi", "ntn": "0786543-2"}',
        '{"business_name": "Lucky Cement Limited", "city": "Karachi"}',
        '{"company_name": "Broken Search Ltd"}',
        '{"company_name": "", "business_name": null}',
        '{"company_name": "ENGRO CORPORATION LTD"}',
    ]) + "\n")
    loader = FakeLoader()
    stats = run(job_config, loader)
    out = job_config.output_dir

    assert stats.status == "completed"
    assert stats.items == {"success": 2, "failed": 1, "skipped": 2}

    results = read(out, "fbr_company_search_results")
    engro = [r for r in results if r["company_key"] == "engro corporation"]
    assert len(engro) == 4
    assert {r["result_url"] for r in engro if r["likely_domain"]} == {"https://www.engro.com/",
                                                                     "https://www.engro.com/contact-us/"}
    assert engro[0]["job_name"] == "test_job" and engro[0]["loader"] == "fake" and engro[0]["source"] == "fbr"
    assert "111211211" in engro[0]["phone_numbers"]

    summaries = {s["company_key"]: s for s in read(out, "fbr_company_enrichment_summary")}
    assert summaries["engro corporation"]["likely_domain"] == "engro.com"
    assert summaries["engro corporation"]["ntn"] == "0786543-2"
    assert summaries["engro corporation"]["pages_fetched"] >= 2
    assert "info@engro.com" in summaries["engro corporation"]["emails"]
    assert summaries["lucky cement"]["likely_domain"] == "lucky-cement.com"

    domains = read(out, "fbr_company_domains")
    assert {d["domain"] for d in domains if d["likely_domain"]} == {"engro.com", "lucky-cement.com"}
    assert read(out, "fbr_company_metadata")
    assert {c["source_type"] for c in read(out, "fbr_company_contacts")} == {"search_snippet", "website"}

    skipped = read(out, "skipped")
    assert sorted(s["reason"] for s in skipped) == ["duplicate_company", "missing_company_name"]
    errors = read(out, "errors")
    assert [e["stage"] for e in errors] == ["search"]
    assert errors[0]["company_key"] == "broken search"
    assert errors[0]["attempts"] == 2

    # Re-run: finished companies and skipped rows are not touched again; only the failed one is retried.
    loader2 = FakeLoader()
    stats2 = run(job_config, loader2, resume=True)
    assert stats2.job_id == stats.job_id
    assert stats2.items == {"already_done": 2, "failed": 1}
    assert all("Broken" in url for url in loader2.calls)
    assert len(read(out, "fbr_company_search_results")) == len(results)
    assert len(read(out, "skipped")) == 2


def test_limit_and_restart(job_config):
    job_config.input_file.write_text('{"company_name": "Engro Corporation"}\n{"company_name": "Lucky Cement"}\n')
    assert run(job_config, FakeLoader(), limit=1).items == {"success": 1}
    assert run(job_config, FakeLoader()).items == {"already_done": 1, "success": 1}
    assert run(job_config, FakeLoader(), restart=True).items == {"success": 2}


def test_search_bot_challenge_is_retried_without_cache(job_config):
    challenge = '<html><div class="anomaly-modal__title">bots use DuckDuckGo too</div></html>'

    class ChallengedOnce(FakeLoader):
        async def _fetch_once(self, url: str, *, render_js: bool) -> PageResponse:
            if "duckduckgo" in url and not any("duckduckgo" in c for c in self.calls):
                self.calls.append(url)
                return PageResponse(url=url, final_url=url, status_code=202, html=challenge, latency_ms=1,
                                    loader=self.name)
            return await super()._fetch_once(url, render_js=render_js)

    job_config.input_file.write_text('{"company_name": "Engro Corporation"}\n')
    loader = ChallengedOnce()
    assert run(job_config, loader).items == {"success": 1}
    assert sum("duckduckgo" in url for url in loader.calls) >= 2


def test_google_ai_overview_and_duckduckgo_fallback(job_config):
    class SearchLoader(FakeLoader):
        async def _fetch_once(self, url: str, *, render_js: bool) -> PageResponse:
            if "google.com/search" in url:
                self.calls.append(url)
                html = CAPTCHA if "Engro" in url else ACME
                return PageResponse(url=url, final_url=url, status_code=200, html=html, latency_ms=1,
                                    loader=self.name)
            return await super()._fetch_once(url, render_js=render_js)

    google = SearchEngineConfig(name="google", resolve_links=False, cooldown_seconds=0,
                                retry={"max_attempts": 1, "backoff_seconds": 0})
    config = job_config.model_copy(update={"search": SearchConfig(
        engines=[google, SearchEngineConfig(name="duckduckgo")])})
    config.input_file.write_text('{"company_name": "Acme Traders"}\n{"company_name": "Engro Corporation"}\n')
    stats = run(config, SearchLoader())
    out = config.output_dir
    assert stats.items == {"success": 2}

    [summary] = read(out, "fbr_company_search_summaries")
    assert summary["company_key"] == "acme traders" and summary["engine"] == "google"
    assert summary["summary_type"] == "ai_overview" and summary["text"].startswith("Acme Traders is a Karachi-based")
    assert summary["sources"][0]["url"] == "https://acme.pk/about" and summary["source_domains"] == ["acme.pk"]
    assert summary["emails"] == ["info@acme.pk"]
    contacts = [c for c in read(out, "fbr_company_contacts") if c["source_type"] == "search_summary"]
    assert [c["company_key"] for c in contacts] == ["acme traders"]

    results = read(out, "fbr_company_search_results")
    assert {r["engine"] for r in results if r["company_key"] == "acme traders"} == {"google"}
    assert {r["engine"] for r in results if r["company_key"] == "engro corporation"} == {"duckduckgo"}
    acme_home = next(r for r in results if r["result_title"] == "Acme Traders | Home")
    assert acme_home["metadata"]["url_resolved"] is False and acme_home["likely_domain"]

    companies = {s["company_key"]: s for s in read(out, "fbr_company_enrichment_summary")}
    assert companies["acme traders"]["search_engines"] == ["google"]
    assert companies["acme traders"]["summary_types"] == ["google:ai_overview"]
    assert companies["acme traders"]["likely_domain"] == "acme.pk"
    assert companies["engro corporation"]["search_engines"] == ["duckduckgo"]
    assert companies["engro corporation"]["likely_domain"] == "engro.com"

    [error] = read(out, "errors")
    assert (error["stage"], error["engine"], error["error_type"]) == ("search", "google", "BlockedError")
    assert error["company_key"] == "engro corporation"


def test_company_profile_from_website_with_local_llm(job_config, monkeypatch):
    prompts: list[str] = []

    def ollama(request: httpx.Request) -> httpx.Response:
        prompts.append(json.loads(request.content)["messages"][0]["content"])
        reply = {"company_name": "Engro Corporation", "summary": "Engro runs fertilizer and energy businesses.",
                 "industry": "Conglomerate", "business_type": "manufacturer", "products_services": ["Fertilizer"],
                 "brands": [], "key_people": [], "certifications": [], "export_markets": [],
                 "locations": ["Karachi"], "partners_clients": [],
                 "city": {"value": "Karachi", "evidence": "Karachi"}, "address": None, "founded_year": None,
                 "employees": None, "legal_form": None, "parent_company": None, "stock_listing": None,
                 "financial_info": None, "production_capacity": None}
        return httpx.Response(200, json={"message": {"content": json.dumps(reply)}})

    monkeypatch.setattr(CompanySummarizer, "transport", httpx.MockTransport(ollama))
    config = job_config.model_copy(update={"summary": job_config.summary.model_copy(update={"enabled": True})})
    config.input_file.write_text('{"company_name": "Engro Corporation", "city": "Karachi"}\n')
    stats = run(config, FakeLoader())
    out = config.output_dir
    assert stats.items == {"success": 1}

    [profile] = read(out, "fbr_company_profiles")
    assert profile["domain"] == "engro.com" and profile["summary"].startswith("Engro runs")
    assert profile["city"] == "Karachi" and profile["evidence"] == {"city": "Karachi"}
    assert profile["source_urls"] and all("engro.com" in url for url in profile["source_urls"])
    assert "info@engro.com" in profile["emails"]
    assert "--- home page https://www.engro.com/" in prompts[0] and "--- search results" in prompts[0]
    [summary] = read(out, "fbr_company_enrichment_summary")
    assert summary["summary"].startswith("Engro runs") and summary["industry"] == "Conglomerate"


def test_unreachable_llm_fails_the_company_for_a_retry(job_config, monkeypatch):
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    monkeypatch.setattr(CompanySummarizer, "transport", httpx.MockTransport(down))
    config = job_config.model_copy(update={"summary": job_config.summary.model_copy(update={"enabled": True})})
    config.input_file.write_text('{"company_name": "Engro Corporation", "city": "Karachi"}\n')
    assert run(config, FakeLoader()).items == {"failed": 1}
    [error] = read(config.output_dir, "errors")
    assert error["stage"] == "summary" and error["error_type"] == "SummaryError"
    assert not read(config.output_dir, "fbr_company_profiles")


def test_linkedin_page_from_the_company_website(job_config):
    class WithLinkedIn(FakeLoader):
        async def _fetch_once(self, url: str, *, render_js: bool) -> PageResponse:
            if "linkedin.com/company/" in url:
                self.calls.append(url)
                return PageResponse(url=url, final_url=url, status_code=200, html=fixture_html("linkedin_company.html"),
                                    latency_ms=1, loader=self.name, render_js=render_js)
            return await super()._fetch_once(url, render_js=render_js)

    linkedin = job_config.linkedin.model_copy(update={"enabled": True})
    config = job_config.model_copy(update={"linkedin": linkedin})
    config.input_file.write_text('{"company_name": "Engro Corporation", "city": "Karachi"}\n')
    loader = WithLinkedIn()
    assert run(config, loader).items == {"success": 1}
    assert "https://www.linkedin.com/company/engro-corporation/" in loader.calls

    [record] = read(config.output_dir / "linkedin", "fbr_company_linkedin_companies")
    assert record["company_key"] == "engro corporation" and record["found_on"] == "website"
    assert record["linkedin_name"] == "Sadaqat Limited" and record["name_matches"] is False  # fixture is another firm
    assert record["industry"] == "Textile Manufacturing" and record["founded_year"] == 1987
    assert record["website_matches_likely_domain"] is False
    assert not list(config.output_dir.glob("*linkedin*.jsonl"))
