"""Company-name resolution and normalization, query building and official-domain detection."""

from __future__ import annotations

from conftest import fixture_html, make_company, make_page

from app.core.input_reader import InputReader, NameNormalizer, RejectedRow
from app.core.models import SearchResult
from app.enrich.domain_detector import DomainDetector, name_match_score
from app.enrich.search_engines import build_query, build_search_url
from app.parse import ParseContext
from app.parse.search_result_parser import SearchResultParser

# ---------------------------------------------------------------- name resolution


def resolve(job_config, rules, *rows):
    reader = InputReader(job_config, rules)
    return [reader.resolve(n, row) for n, row in enumerate(rows, start=1)]


def test_company_name_preferred_over_business_name(job_config, rules):
    [company] = resolve(job_config, rules, {"company_name": "  Engro   Corporation Limited ", "business_name": "Engro"})
    assert company.company_name == "Engro Corporation Limited"
    assert company.business_name == "Engro"
    assert company.resolved_name == "Engro Corporation Limited"
    assert company.search_name == "Engro Corporation"


def test_falls_back_to_business_name(job_config, rules):
    [company] = resolve(job_config, rules, {"company_name": None, "business_name": "Al-Noor Traders (Pvt) Ltd"})
    assert company.company_name is None
    assert company.resolved_name == "Al-Noor Traders (Pvt) Ltd"
    assert company.search_name == "Al-Noor Traders"


def test_missing_names_are_rejected_with_reason(job_config, rules):
    rejected = resolve(job_config, rules, {"company_name": "", "business_name": "  "}, {"city": "Lahore"},
                       {"company_name": "N/A"})
    assert all(isinstance(r, RejectedRow) and r.reason == "missing_company_name" for r in rejected)


def test_duplicate_companies_are_rejected(job_config, rules):
    first, second = resolve(job_config, rules, {"company_name": "Lucky Cement Limited"},
                            {"company_name": "LUCKY CEMENT LTD."})
    assert first.company_key == "lucky cement"
    assert isinstance(second, RejectedRow) and second.reason == "duplicate_company"


def test_field_aliases_and_mapping(job_config, rules):
    [company] = resolve(job_config, rules, {"Company_Name": "Packages Limited", "CITY": "Lahore", "NTN": "0712345-6"})
    assert company.company_name == "Packages Limited"
    assert company.city == "Lahore"
    assert company.ntn == "0712345-6"


def test_input_file_with_bad_lines(job_config, rules):
    job_config.input_file.write_text('{"company_name": "A One Ltd"}\nnot json\n[1, 2]\n\n{"business_name": "B Two"}\n')
    items = list(InputReader(job_config, rules))
    assert [type(i).__name__ for i in items] == ["CompanyInput", "RejectedRow", "RejectedRow", "CompanyInput"]
    assert [i.reason for i in items if isinstance(i, RejectedRow)] == ["invalid_json", "not_an_object"]


def test_name_normalizer(job_config, rules):
    normalizer = NameNormalizer(job_config.name_normalization, rules)
    assert normalizer.search_name("Hashoo Group (SMC-Private) Limited") == "Hashoo Group"
    assert normalizer.search_name("Nishat Mills Ltd.") == "Nishat Mills"
    assert normalizer.search_name("Unlimited Solutions") == "Unlimited Solutions"  # "limited" inside a word stays
    assert normalizer.search_name("Systems Limited") == "Systems Limited"  # one word left: keep the suffix
    assert normalizer.search_name("ABC™ Traders LLP") == "ABC Traders"


# ---------------------------------------------------------------- search query


def test_build_query_and_url():
    company = make_company("Engro Corporation", city="Karachi")
    assert build_query("{name} {city} Pakistan", company) == "Engro Corporation Karachi Pakistan"
    assert build_query("{name} {province} Pakistan", company) == "Engro Corporation Pakistan"
    template = "https://html.duckduckgo.com/html/?q={query}&kl={region}"
    assert build_search_url(template, "a b&c", region="pk-en", page=0, page_size=10) == \
        "https://html.duckduckgo.com/html/?q=a+b%26c&kl=pk-en"
    assert build_search_url(template, "x", region="pk-en", page=1, page_size=10).endswith("&s=10&dc=11")


# ---------------------------------------------------------------- domain detection


def results(*urls: str) -> list[SearchResult]:
    return [SearchResult(rank=i, title=f"Result {i}", url=u, domain=u.split("/")[2]) for i, u in enumerate(urls, 1)]


def test_domain_detection_on_search_results(rules):
    parsed = SearchResultParser(ParseContext(rules)).parse(make_page(fixture_html("ddg_html.html")))
    candidates = DomainDetector(rules).detect(make_company("Engro Corporation"), parsed)
    likely = [c for c in candidates if c.likely_domain]
    assert [c.domain for c in likely] == ["engro.com"]
    assert likely[0].result_urls == ["https://www.engro.com/", "https://www.engro.com/contact-us/"]
    by_domain = {c.domain: c for c in candidates}
    assert by_domain["linkedin.com"].category == "social"
    assert by_domain["dawn.com"].category == "news"


def test_generic_domains_are_never_likely(rules):
    candidates = DomainDetector(rules).detect(make_company("Engro"), results(
        "https://www.facebook.com/engro", "https://www.rozee.pk/company/engro", "https://www.daraz.pk/shop/engro"))
    assert not any(c.likely_domain for c in candidates)
    assert {c.category for c in candidates} == {"social", "directory", "marketplace"}


def test_unrelated_top_result_is_not_likely(rules):
    candidates = DomainDetector(rules).detect(make_company("Lucky Cement"), results("https://www.randomblog.com/x"))
    assert not candidates[0].likely_domain


def test_pk_domain_preferred_when_names_match(rules):
    candidates = DomainDetector(rules).detect(make_company("Nishat Mills"), results(
        "https://nishatmills.com/", "https://www.nishatmills.com.pk/"))
    assert [c.domain for c in candidates if c.likely_domain] == ["nishatmills.com.pk"]


def test_name_match_signals():
    assert "exact_name" in name_match_score("Lucky Cement", "lucky-cement.com")[1]
    assert "acronym" in name_match_score("Pakistan State Oil", "pso.com.pk")[1]
    assert name_match_score("Engro Corporation", "dawn.com")[0] == 0
