from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import JobConfig, load_extraction_rules
from app.core.models import CompanyInput, PageResponse

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_html(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def make_page(html: str, url: str = "https://example.com/") -> PageResponse:
    return PageResponse(url=url, final_url=url, status_code=200, html=html, latency_ms=1, loader="test")


def make_company(name: str, city: str | None = "Karachi") -> CompanyInput:
    key = name.lower()
    return CompanyInput(row_number=1, company_key=key, company_name=name, resolved_name=name, search_name=name,
                        city=city)


@pytest.fixture
def rules():
    return load_extraction_rules()


@pytest.fixture
def job_config(tmp_path: Path) -> JobConfig:
    return JobConfig(job_name="test_job", job_type="fbr_company_enrichment", input_file=tmp_path / "input.jsonl",
                     output_dir=tmp_path / "out", state_dir=tmp_path / "state", output_prefix="fbr_company",
                     parsers=["search_result_parser", "contact_extractor", "metadata_extractor"],
                     retry={"max_attempts": 3, "backoff_seconds": 0, "max_backoff_seconds": 0},
                     rate_limit={"requests_per_second": 1000, "max_concurrency": 2})
