"""Scraping engine: extraction, message contracts, the LLM guard and site plugins; Gold domain-profile merging."""

import json

import pytest

from data_platform.errors import PermanentError
from data_platform.scrape import sites
from data_platform.scrape.enricher import build_context, validate
from data_platform.scrape.extract import decode, follow_links, parse_html, social_profile
from data_platform.scrape.messages import SCHEMA_VERSION, FetchRequest, ParsedPage, json_schema
from data_platform.gold.domain_profiles import CityIndex, build_profile, clean_name

HOME = """<!doctype html><html lang="en"><head><title>Acme Pakistan | Home</title>
<meta name="description" content="Acme makes cement.">
<meta property="og:site_name" content="Acme">
<script type="application/ld+json">{"@context": "https://schema.org", "@type": "Corporation",
 "legalName": "Acme Cement Ltd", "foundingDate": "1998-05-01", "telephone": "042 111 222 333",
 "address": {"@type": "PostalAddress", "streetAddress": "12 Main Blvd", "addressLocality": "Gulberg",
             "addressRegion": "Lahore"},
 "sameAs": ["https://www.facebook.com/acmepk", "https://twitter.com/acme_pk"]}</script>
<script>var tracking = "ignore me";</script></head>
<body><nav><a href="/about-us">About</a> <a href="https://acme.pk/contact">Contact</a>
<a href="/news/2024/about-our-new-plant">News about</a> <a href="https://other.pk/about">Partner</a></nav>
<p>Welcome to Acme.</p>
<a href="https://www.facebook.com/sharer/sharer.php?u=x">Share</a>
<a href="https://www.instagram.com/acme.pk/">Instagram</a>
<a href="https://www.linkedin.com/company/acme-cement/mycompany/verification">LinkedIn</a>
<a href="https://www.youtube.com/watch?v=abc">A video</a>
<a href="https://api.whatsapp.com/send?phone=+92 300 1234567">WhatsApp</a>
<a href="mailto:Info@Acme.pk?subject=hi">Mail</a> <a href="tel:+92-42-111-222-333">Call</a></body></html>"""


def test_social_profiles_keep_profiles_and_drop_share_buttons_and_posts():
    assert social_profile("https://m.facebook.com/acmepk/?ref=x") == ("facebook", "https://www.facebook.com/acmepk")
    assert social_profile("https://www.facebook.com/sharer/sharer.php?u=x") is None
    assert social_profile("https://twitter.com/intent/tweet?text=x") is None
    assert social_profile("https://twitter.com/acme_pk") == ("x_twitter", "https://x.com/acme_pk")
    assert social_profile("https://www.instagram.com/p/Cxyz/") is None
    assert social_profile("https://www.linkedin.com/company/acme/about/") == ("linkedin",
                                                                              "https://www.linkedin.com/company/acme")
    assert social_profile("https://www.youtube.com/watch?v=abc") is None
    assert social_profile("https://www.youtube.com/@acme") == ("youtube", "https://www.youtube.com/@acme")
    assert social_profile("https://api.whatsapp.com/send") is None
    assert social_profile("https://api.whatsapp.com/send?phone=923001234567") == ("whatsapp",
                                                                                  "https://wa.me/923001234567")
    assert social_profile("https://example.com/acme") is None


def test_follow_links_keeps_same_site_company_pages_only():
    links = follow_links("https://acme.pk/", ["/about-us", "https://www.acme.pk/contact", "/news/2024/about-x",
                                              "https://other.pk/about", "/about-us#team", "mailto:x@acme.pk"], "acme.pk")
    assert [(link["url"], link["kind"]) for link in links] == [("https://acme.pk/about-us", "about"),
                                                             ("https://www.acme.pk/contact", "contact")]


def test_parse_html_extracts_markup_links_contacts_and_clean_text():
    page = parse_html(HOME, url="https://acme.pk/", domain="acme.pk", country="PK", kind="home")
    facts = {(f["field"], f["value"], f["source"]) for f in page["facts"]}
    assert ("company_name", "Acme Cement Ltd", "jsonld") in facts
    assert ("founded_year", "1998", "jsonld") in facts
    assert ("address_locality", "Gulberg", "jsonld") in facts
    assert ("phone", "+9242111222333", "jsonld") in facts
    assert ("social.facebook", "https://www.facebook.com/acmepk", "jsonld") in facts
    assert ("social.x_twitter", "https://x.com/acme_pk", "jsonld") in facts
    assert ("social.instagram", "https://www.instagram.com/acme.pk", "link") in facts
    assert ("social.linkedin", "https://www.linkedin.com/company/acme-cement", "link") in facts
    assert ("social.whatsapp", "https://wa.me/923001234567", "link") in facts
    assert ("email", "info@acme.pk", "link") in facts
    assert ("company_name", "Acme", "meta") in facts
    assert not any(f[0] == "social.youtube" for f in facts)
    assert page["title"] == "Acme Pakistan | Home" and page["lang"] == "en"
    assert "Welcome to Acme." in page["text"] and "ignore me" not in page["text"]
    assert [link["kind"] for link in page["links"]] == ["about", "contact"]


def test_decode_uses_the_declared_charset():
    body = '<html><head><meta charset="windows-1252"></head><body>caf\xe9</body></html>'.encode("cp1252")
    assert "café" in decode(body, None)
    assert "café" in decode("café".encode("utf-8"), "text/html; charset=UTF-8")


def test_messages_round_trip_and_reject_unknown_versions():
    request = FetchRequest(request_id="r1", campaign_id=1, domain="acme.pk", country="PK", url="https://acme.pk/",
                           kind="home", depth=0, site="default")
    assert FetchRequest.from_json(request.to_json()) == request
    with pytest.raises(PermanentError):
        FetchRequest.from_json(request.to_json().replace(f'"v":{SCHEMA_VERSION}', '"v":99'))
    with pytest.raises(PermanentError):
        FetchRequest.from_json('{"v": 1, "url": "https://acme.pk/"}')
    schema = json_schema(ParsedPage)
    assert "facts" not in schema["required"] and "request_id" in schema["required"]


CITIES = CityIndex(["Lahore", "Karachi", "Model Town", "Gulberg"])       # most populous first
PRIORITY = {"opencorporates": 90, "wikidata": 80, "website": 70}


def _fact(field, value, method, kind="home", source="website"):
    return {"field": field, "value": value, "source": source, "method": method, "evidence_type": kind,
            "evidence_url": f"https://acme.pk/{kind}" if kind not in ("domain", "record") else None}


def _profile(facts, **kwargs):
    return build_profile("acme.pk", "PK", facts, cities=CITIES, source_priority=PRIORITY, **kwargs)


def test_profile_precedence_votes_and_provenance():
    facts = [
        _fact("company_name", "Acme - Building Pakistan", "jsonld"), _fact("company_name", "www.acme.pk", "meta"),
        _fact("founded_year", "1998", "jsonld"), _fact("founded_year", "1998", "jsonld", "about"),
        _fact("founded_year", "2001", "jsonld", "contact"), _fact("founded_year", "1990", "llm", "domain"),
        _fact("city", "Model Town", "jsonld"), _fact("address", "4K Model Town, Lahore", "jsonld"),
        _fact("summary", "Acme makes cement.", "llm", "domain"), _fact("description", "Meta text", "meta"),
        _fact("social.facebook", "https://www.facebook.com/a", "link"),
        _fact("social.facebook", "https://www.facebook.com/b", "link", "about"),
        _fact("social.facebook", "https://www.facebook.com/b", "link", "contact"),
        _fact("email", "info@acme.pk", "link"), _fact("email", "info@acme.pk", "text", "contact"),
        _fact("category_group", "Industry & Craft", "llm", "domain"),
        _fact("founded_year", "1997", "record", "record", source="wikidata"),
    ]
    profile = _profile(facts, organization_id="org_1", fallback_group="Retail")
    sources = json.loads(profile["field_sources"])
    assert profile["company_name"] == "Acme"
    assert profile["founded_year"] == 1998 and sources["founded_year"] == "website:jsonld"
    assert profile["city"] == "Lahore"                      # most populous place mentioned, not the neighbourhood
    assert profile["summary"] == "Acme makes cement." and sources["summary"] == "website:llm"
    assert profile["facebook"] == "https://www.facebook.com/b"
    assert profile["emails"] == ["info@acme.pk"]
    assert profile["category_group"] == "Industry & Craft"
    assert profile["sources"] == ["website", "wikidata"] and profile["pages_crawled"] == 3
    assert profile["organization_id"] == "org_1"


def test_profile_falls_back_to_records_and_custom_parsers_win():
    record = [_fact("company_name", "Acme Cement", "record", "record", source="wikidata"),
              _fact("founded_year", "1997-01-01", "record", "record", source="wikidata"),
              _fact("city", "Karachi", "record", "record", source="wikidata"),
              _fact("social", "https://twitter.com/acme", "record", "record", source="wikidata"),
              _fact("social.x_twitter", "https://x.com/other", "link")]
    profile = _profile(record + [_fact("founded_year", "2000", "llm", "domain")], fallback_group="Retail")
    assert (profile["founded_year"], profile["city"], profile["company_name"]) == (1997, "Karachi", "Acme Cement")
    assert profile["x_twitter"] == "https://x.com/acme"     # a stated profile beats a merely linked one
    assert profile["category_group"] == "Retail"            # nothing better than the businesses' main category
    custom = _profile(record + [_fact("founded_year", "1995", "site")])
    assert (custom["founded_year"], json.loads(custom["field_sources"])["founded_year"]) == \
        (1995, "website:site")


def test_records_from_two_sources_tie_break_by_source_priority():
    facts = [_fact("employees", "500", "record", "record", source="wikidata"),
             _fact("employees", "650", "record", "record", source="opencorporates")]
    assert _profile(facts)["employees"] == "650"


def test_clean_name():
    assert clean_name("InstaCare | Find A Doctor") == "InstaCare"
    assert clean_name("Coca-Cola") == "Coca-Cola"
    assert clean_name("www.acme.pk") is None


def test_city_index_prefers_the_most_populous_place():
    assert CITIES.match("4K, Block K, Model Town, Lahore") == "Lahore"
    assert CITIES.match("gulberg") == "Gulberg" and CITIES.match("Nowhere") is None


def test_llm_answers_need_evidence_from_the_text():
    text = "Acme was founded in 1998 in Lahore. We employ over 500 people."
    answer = {"company_name": "Acme", "summary": "Makes cement.", "industry": "Cement", "category_group": "Bogus",
              "city": {"value": "Lahore", "evidence": "founded in 1998 in Lahore"},
              "founded_year": {"value": 1998, "evidence": "Acme was founded in 1998"},
              "employees": {"value": "500", "evidence": "We employ over 500 people."},
              "market_cap": {"value": "PKR 10bn", "evidence": "market cap of PKR 10bn"},       # not in the text
              "financial_info": None}
    facts = {f["field"]: f["value"] for f in validate(answer, text)}
    assert facts == {"company_name": "Acme", "summary": "Makes cement.", "industry": "Cement", "city": "Lahore",
                     "founded_year": "1998", "employees": "500"}
    wrong_year = {**answer, "founded_year": {"value": 2001, "evidence": "Acme was founded in 1998"}}
    assert "founded_year" not in {f["field"] for f in validate(wrong_year, text)}


def test_llm_context_puts_company_pages_first_within_budget():
    pages = [{"kind": "home", "url": "https://a.pk/", "title": "Home", "text": "h" * 5000},
             {"kind": "about", "url": "https://a.pk/about", "title": "About", "text": "a" * 5000}]
    context = build_context(pages, 1000)
    assert context.index("about page") < context.index("home page") and len(context) <= 1010


def test_site_plugins_resolve_by_domain_and_tag_the_parser_version():
    assert sites.site_for("marham.pk").name == "marham"
    assert sites.site_for("unknown.pk").name == "default"
    assert sites.get("nope").name == "default"
    marham = sites.get("marham")
    assert marham.parser_tag == "+marham@1" and marham.seeds("marham.pk")[1] == ("https://www.marham.pk/about",
                                                                                   "about")
