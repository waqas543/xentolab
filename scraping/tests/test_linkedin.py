"""Public LinkedIn company page: parsing, login-wall handling, picking the right page, and output location."""

from __future__ import annotations

import asyncio

import pytest
from conftest import fixture_html, make_company, make_page

from app.core.config import JobConfig, LinkedInConfig, load_extraction_rules
from app.core.exceptions import BlockedError, ParseError
from app.core.models import LinkedInCompanyRecord, PageResponse, SearchResult
from app.enrich.linkedin_enricher import LinkedInEnricher, find_candidates, name_matches
from app.fetch.fetcher import Fetcher
from app.fetch.retry import RetryPolicy
from app.loader.base import PageLoader
from app.parse import ParseContext
from app.parse.linkedin_parser import LinkedInCompanyParser, company_slug, parse_count
from app.writer import OutputManager

URL = "https://www.linkedin.com/company/sadaqatgroup/"
LOGIN_PAGE = ("<html><head><title>LinkedIn Login, Sign in | LinkedIn</title></head>"
              "<body><h1>Sign in</h1></body></html>")


@pytest.fixture
def parser(rules) -> LinkedInCompanyParser:
    return LinkedInCompanyParser(ParseContext(rules))


def test_parses_public_company_page(parser):
    company = parser.parse(make_page(fixture_html("linkedin_company.html"), URL))
    assert company.name == "Sadaqat Limited"
    assert company.industry == "Textile Manufacturing" and company.organization_type == "Public Company"
    assert company.website == "http://www.sadaqatgroup.net"  # unwrapped from LinkedIn's redirect link
    assert company.company_size == "10,001+ employees" and company.headquarters == "Faisalabad, Punjab"
    assert company.founded_year == 1987 and company.specialties == ["Home Textile", "Apparel"]
    assert company.followers == 67771 and company.employees_on_linkedin == 1970
    assert company.locations == ["02 Km-Sahianwala Road, Khurrianwala Faisalabad, Punjab 37630, PK"]
    assert company.description.startswith("Half a century ago, a textile trading family")
    assert company.tagline.startswith("Vertically Integrated Textile Manufacturer")
    assert company.logo_url.startswith("https://media.licdn.com/")
    assert company.recent_posts and company.recent_posts[0].likes == 20
    assert company.recent_posts[0].text.startswith("Every great piece of denim")


def test_login_wall_and_unknown_pages(parser):
    with pytest.raises(BlockedError):
        parser.parse(make_page(LOGIN_PAGE, URL))
    with pytest.raises(BlockedError):
        parser.parse(make_page('<html><script>window.location.href = "https://www.linkedin.com/authwall"'
                               "</script></html>", URL))
    with pytest.raises(ParseError):
        parser.parse(make_page("<html><title>Page not found | LinkedIn</title></html>", URL))


def test_helpers():
    assert company_slug("https://pk.linkedin.com/company/SadaqatGroup/about?trk=x") == "sadaqatgroup"
    assert company_slug("https://www.linkedin.com/in/someone") is None
    assert (parse_count("67,771"), parse_count("1.2K"), parse_count(None)) == (67771, 1200, None)
    assert name_matches("Sadaqat Limited", "sadaqatgroup")
    assert name_matches("Al Syed Shadab Flour General Mills", "Syed Shadab Flour Mills")
    assert not name_matches("Al Syed Shadab Flour General Mills", "Bismillah Flour Mills")
    assert not name_matches("Engro Corporation", "Lucky Cement")


def result(url: str, title: str) -> SearchResult:
    return SearchResult(rank=1, title=title, url=url, domain="linkedin.com")


def test_candidates_prefer_the_website_and_check_names():
    company = make_company("Sadaqat Limited")
    results = [result("https://pk.linkedin.com/company/other-textiles", "Other Textiles | LinkedIn"),
               result("https://www.linkedin.com/company/40844851", "Sadaqat Limited | LinkedIn"),
               result("https://pk.linkedin.com/company/sadaqat-ltd", "Sadaqat Limited | LinkedIn"),
               result("https://www.linkedin.com/in/someone", "Someone - Sadaqat Limited | LinkedIn")]
    candidates = find_candidates(company, ["https://www.linkedin.com/company/sadaqatgroup/"], results, [])
    assert [(c.slug, c.found_on) for c in candidates] == [("sadaqatgroup", "website"), ("sadaqat-ltd", "search")]
    assert candidates[0].url == URL
    assert find_candidates(company, [], results[:2], []) == []


class WallThenPage(PageLoader):
    name = "fake"

    def __init__(self, walls: int) -> None:
        super().__init__(retry=RetryPolicy(max_attempts=1, backoff_seconds=0, max_backoff_seconds=0))
        self.walls = walls
        self.calls = 0

    async def _fetch_once(self, url: str, *, render_js: bool) -> PageResponse:
        self.calls += 1
        html = LOGIN_PAGE if self.calls <= self.walls else fixture_html("linkedin_company.html")
        return PageResponse(url=url, final_url=url, status_code=200, html=html, latency_ms=1, loader=self.name,
                            render_js=render_js)


def linkedin_enricher(tmp_path, loader: PageLoader, attempts: int = 3) -> LinkedInEnricher:
    job = JobConfig(job_name="t", output_dir=tmp_path, state_dir=tmp_path)
    config = LinkedInConfig(enabled=True, max_posts=2,
                            retry={"max_attempts": attempts, "backoff_seconds": 0, "max_backoff_seconds": 0})
    return LinkedInEnricher(config, job, Fetcher(loader), load_extraction_rules())


def test_login_wall_is_retried(tmp_path):
    loader = WallThenPage(walls=2)
    [candidate] = find_candidates(make_company("Sadaqat Limited"), [URL], [], [])
    fetched = asyncio.run(linkedin_enricher(tmp_path, loader).fetch(make_company("Sadaqat Limited"), candidate))
    assert loader.calls == 3 and fetched.name_matches and fetched.found_on == "website"
    assert len(fetched.company.recent_posts) == 2


def test_persistent_login_wall_gives_up(tmp_path):
    loader = WallThenPage(walls=10)
    [candidate] = find_candidates(make_company("Sadaqat Limited"), [URL], [], [])
    with pytest.raises(BlockedError):
        asyncio.run(linkedin_enricher(tmp_path, loader, attempts=2).fetch(make_company("Sadaqat Limited"),
                                                                           candidate))
    assert loader.calls == 2


def test_linkedin_records_go_to_their_own_folder(tmp_path):
    config = JobConfig(job_name="t", output_dir=tmp_path, state_dir=tmp_path, output_prefix="fbr_company")
    output = OutputManager.from_config(config)
    output.write(LinkedInCompanyRecord(record_id="r", source="fbr", job_id="j", job_name="t", loader="l",
                                       row_number=1, linkedin_url=URL, found_on="website", name_matches=True,
                                       linkedin_name="Sadaqat Limited"))
    output.close()
    assert (tmp_path / "linkedin" / "fbr_company_linkedin_companies_0001.jsonl").read_text().count("\n") == 1
    assert not list(tmp_path.glob("*.jsonl"))
