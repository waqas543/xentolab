"""Company profile from website text: context building, the evidence guard, and the Ollama client (mocked)."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.core.config import SummaryConfig
from app.enrich.company_summarizer import (
    CompanySummarizer,
    SummaryError,
    build_context,
    page_kind,
    validate,
)

TEXT = """--- about page https://acme.pk/about-us
Title: About Acme
Acme Flour Mills (Private) Limited was established in 1998 in Faisalabad. We employ over 250 people.
Our head office: 12-B Industrial Estate, Faisalabad, Pakistan. Capacity of 400 tons per day.
Products: Fine Atta, Maida, Suji and Bran under the brand Golden Wheat. ISO 22000 and Halal certified.
CEO Muhammad Tariq leads the company. We export to Afghanistan and the UAE.
--- search results
- Acme Flour Mills (acme.pk): Leading flour mill in Faisalabad."""


def answer(**overrides) -> dict:
    data = {
        "company_name": "Acme Flour Mills (Private) Limited",
        "summary": "Acme mills wheat flour for retail and industrial buyers.",
        "industry": "Flour Milling", "business_type": "manufacturer",
        "products_services": ["Fine Atta", "Maida", "Suji", "Bran", "Rice"],
        "brands": ["Golden Wheat", "Silver Wheat"],
        "key_people": [{"name": "Muhammad Tariq", "role": "CEO"}, {"name": "Ali Khan", "role": "CFO"}],
        "certifications": ["ISO 22000", "Halal"], "export_markets": ["Afghanistan", "UAE", "China"],
        "locations": ["Faisalabad"], "partners_clients": [],
        "city": {"value": "Faisalabad", "evidence": "established in 1998 in Faisalabad"},
        "address": {"value": "12-B Industrial Estate, Faisalabad",
                    "evidence": "12-B Industrial Estate, Faisalabad, Pakistan"},
        "founded_year": {"value": 1998, "evidence": "was established in 1998"},
        "employees": {"value": "250", "evidence": "We employ over 250 people"},
        "legal_form": {"value": "(Private) Limited", "evidence": "Acme Flour Mills (Private) Limited"},
        "parent_company": None, "stock_listing": None,
        "financial_info": {"value": "Revenue PKR 2 billion", "evidence": "Revenue of PKR 2 billion"},
        "production_capacity": {"value": "400 tons per day", "evidence": "Capacity of 400 tons per day"},
    }
    data.update(overrides)
    return data


def test_evidence_guard_keeps_supported_facts_and_drops_invented_ones():
    profile = validate(answer(), TEXT, name="Acme Flour Mills", domain="acme.pk", model="m")
    assert profile.summary and profile.industry == "Flour Milling" and profile.business_type == "manufacturer"
    assert profile.products_services == ["Fine Atta", "Maida", "Suji", "Bran"]
    assert profile.brands == ["Golden Wheat"]
    assert [(p.name, p.role) for p in profile.key_people] == [("Muhammad Tariq", "CEO")]
    assert profile.export_markets == ["Afghanistan", "UAE"]
    assert (profile.city, profile.founded_year, profile.employees) == ("Faisalabad", 1998, "250")
    assert profile.legal_form == "(Private) Limited" and profile.production_capacity == "400 tons per day"
    assert profile.address == "12-B Industrial Estate, Faisalabad"
    assert profile.financial_info is None  # quote is not in the text
    assert profile.evidence["founded_year"] == "was established in 1998"
    assert set(profile.dropped) == {"products_services:Rice", "brands:Silver Wheat", "key_people:Ali Khan",
                                    "export_markets:China", "financial_info:Revenue PKR 2 billion"}


def test_value_must_appear_in_its_quote():
    profile = validate(answer(founded_year={"value": 2001, "evidence": "was established in 1998"},
                              city={"value": "Lahore", "evidence": "established in 1998 in Faisalabad"},
                              business_type="spaceship"),
                       TEXT, name="Acme", domain="acme.pk", model="m")
    assert profile.founded_year is None and profile.city is None and profile.business_type is None
    assert "founded_year:2001" in profile.dropped and "city:Lahore" in profile.dropped


def test_build_context_orders_pages_and_respects_budget():
    pages = [("home", "https://a.pk/", "Home", "H" * 5000), ("about", "https://a.pk/about", "About", "A" * 5000)]
    context = build_context(pages, ["snippet one", "snippet two"], budget=4000)
    assert context.index("about page") < context.index("home page") < context.index("search results")
    assert len(context) <= 4100 and "- snippet one" in context
    assert build_context([], ["only snippets"], budget=1000).startswith("--- search results")


def test_page_kind():
    assert page_kind("https://a.pk/") == "home"
    assert page_kind("https://a.pk/about-us/") == "about"
    assert page_kind("https://a.pk/our-products") == "products"
    assert page_kind("https://a.pk/management-team") == "team"
    assert page_kind("https://a.pk/contact") == "contact"
    assert page_kind("https://a.pk/news/2024") == "page"


def summarizer_with(monkeypatch, handler) -> CompanySummarizer:
    monkeypatch.setattr(CompanySummarizer, "transport", httpx.MockTransport(handler))
    return CompanySummarizer(SummaryConfig(enabled=True, model="Qwen3.5:4B"), "http://ollama.test")


def test_summarizer_request_and_retry_on_bad_json(monkeypatch):
    requests: list[dict] = []
    replies = iter(["not json", json.dumps(answer())])

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"message": {"content": next(replies)}})

    summarizer = summarizer_with(monkeypatch, handler)
    profile = asyncio.run(summarizer.summarize(name="Acme Flour Mills", domain="acme.pk", text=TEXT))
    assert len(requests) == 2 and profile.founded_year == 1998 and profile.model == "Qwen3.5:4B"
    body = requests[0]
    assert body["model"] == "Qwen3.5:4B" and body["think"] is False and body["options"]["temperature"] == 0
    assert body["format"]["type"] == "object" and "acme.pk" in body["messages"][0]["content"]


def test_summarizer_unreachable_is_a_retryable_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(SummaryError):
        asyncio.run(summarizer_with(monkeypatch, handler).summarize(name="Acme", domain="acme.pk", text=TEXT))


def test_summarizer_gives_up_on_unusable_answers(monkeypatch):
    summarizer = summarizer_with(monkeypatch, lambda r: httpx.Response(200, json={"message": {"content": "[]"}}))
    with pytest.raises(ValueError):
        asyncio.run(summarizer.summarize(name="Acme", domain="acme.pk", text=TEXT))
