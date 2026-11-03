"""DuckDuckGo parsing, Pakistani phone numbers, emails, social links, locations and page metadata."""

from __future__ import annotations

import pytest
from conftest import fixture_html, make_page

from app.core.exceptions import BlockedError, ParseError
from app.parse import ParseContext, create_parsers
from app.parse.contact_extractor import (
    ContactExtractor,
    extract_emails,
    extract_phones,
    extract_social_links,
    location_matcher,
)
from app.parse.html_parser import HtmlDocument
from app.parse.metadata_extractor import MetadataExtractor
from app.parse.search_result_parser import SearchResultParser, unwrap_ddg_url


@pytest.fixture
def parsers(rules):
    return create_parsers(["search_result_parser", "contact_extractor", "metadata_extractor"], ParseContext(rules))


# ---------------------------------------------------------------- DuckDuckGo


def test_ddg_html_layout(parsers):
    results = parsers["search_result_parser"].parse(make_page(fixture_html("ddg_html.html")))
    assert [r.url for r in results] == [
        "https://www.engro.com/",
        "https://pk.linkedin.com/company/engro-corporation",
        "https://www.engro.com/contact-us/",
        "https://www.dawn.com/news/123/engro-results",
    ]  # ad dropped, utm duplicate of the homepage dropped
    first = results[0]
    assert first.rank == 1
    assert first.title == "Engro Corporation Limited | Home"
    assert first.domain == "engro.com"
    assert first.display_url == "www.engro.com"
    assert "Harbor Front" in first.snippet
    assert [r.rank for r in results] == [1, 2, 3, 4]


def test_ddg_js_layout(parsers):
    results = parsers["search_result_parser"].parse(make_page(fixture_html("ddg_js.html")))
    assert [r.url for r in results] == ["https://www.lucky-cement.com/", "https://www.facebook.com/luckycementpk"]
    assert results[0].title == "Lucky Cement Limited - Home"
    assert results[0].snippet.startswith("Lucky Cement, 6-A")


def test_ddg_bot_challenge_raises_blocked(parsers):
    html = '<html><body><div class="anomaly-modal__title">Unfortunately, bots use DuckDuckGo too.</div></body></html>'
    with pytest.raises(BlockedError):
        parsers["search_result_parser"].parse(make_page(html))


def test_ddg_no_results_is_empty_but_unknown_layout_fails(parsers):
    parser: SearchResultParser = parsers["search_result_parser"]
    assert parser.parse(make_page('<html><div class="no-results">No results.</div></html>')) == []
    with pytest.raises(ParseError):
        parser.parse(make_page("<html><body><p>something else entirely</p></body></html>"))


def test_unwrap_ddg_url():
    assert unwrap_ddg_url("//duckduckgo.com/l/?uddg=https%3A%2F%2Fa.pk%2Fx%3Fy%3D1&rut=z") == "https://a.pk/x?y=1"
    assert unwrap_ddg_url("https://direct.example/") == "https://direct.example/"


# ---------------------------------------------------------------- phones


@pytest.mark.parametrize(("text", "number", "kind"), [
    ("Call 0300-1234567", "+923001234567", "mobile"),
    ("Cell: 0321 7654321", "+923217654321", "mobile"),
    ("+92 300 1234567", "+923001234567", "mobile"),
    ("92 321 7654321", "+923217654321", "mobile"),
    ("0092-333-1112223", "+923331112223", "mobile"),
    ("Tel (042) 35761234", "+924235761234", "landline"),
    ("+92-21-35297501", "+922135297501", "landline"),
    ("051-2345678", "+92512345678", "landline"),
    ("(021) 111-222-333", "+9221111222333", "uan"),
    ("UAN 111-211-211", "111211211", "uan"),
    ("Toll free 0800-12345", "+9280012345", "toll_free"),
])
def test_pakistani_phone_formats(text, number, kind):
    phones = extract_phones(text)
    assert [(p.number, p.kind) for p in phones] == [(number, kind)]


def test_phone_false_positives_are_ignored():
    text = "NTN 1234567-8, date 2024-10-14, price Rs 1,250,000, invoice 98765432, year 1965"
    assert extract_phones(text) == []


def test_phones_are_deduplicated():
    phones = extract_phones("0300-1234567 / +92 300 1234567 / 03001234567")
    assert [p.number for p in phones] == ["+923001234567"]


# ---------------------------------------------------------------- emails, social, locations


def test_emails():
    text = "Mail INFO@Engro.com, sales@lucky-cement.com. Not: logo@2x.png, user@example.com, a@b"
    assert extract_emails(text) == ["info@engro.com", "sales@lucky-cement.com"]


def test_social_links():
    links = extract_social_links([
        "https://www.linkedin.com/company/engro-corporation/",
        "https://www.facebook.com/sharer/sharer.php?u=x",
        "https://www.facebook.com/EngroCorporation",
        "https://twitter.com/intent/tweet?text=x",
        "https://x.com/EngroCorp",
        "https://www.youtube.com/@EngroCorp",
        "https://www.instagram.com/p/abc123/",
        "https://example.com/",
    ])
    assert links == {
        "linkedin": ["https://www.linkedin.com/company/engro-corporation"],
        "facebook": ["https://www.facebook.com/EngroCorporation"],
        "twitter": ["https://x.com/EngroCorp"],
        "youtube": ["https://www.youtube.com/@EngroCorp"],
    }


def test_locations(rules):
    text = "Head Office: 7th Floor, The Harbor Front Building, Clifton, Karachi. Branches in LAHORE and KPK."
    locations = location_matcher(rules).extract(text)
    addresses = [loc for loc in locations if loc.kind == "address"]
    assert addresses[0].text == "7th Floor, The Harbor Front Building, Clifton, Karachi"
    assert addresses[0].city == "Karachi"
    assert addresses[0].province == "Sindh"
    assert {loc.text for loc in locations if loc.kind == "city"} == {"Karachi", "Lahore"}
    assert {loc.text for loc in locations if loc.kind == "province"} == {"Khyber Pakhtunkhwa"}


def test_common_words_are_not_cities(rules):
    assert location_matcher(rules).extract("a tech hub with swat teams in mardan-like style") == []


# ---------------------------------------------------------------- full page


def test_contact_extractor_on_company_site(parsers):
    extractor: ContactExtractor = parsers["contact_extractor"]
    info = extractor.parse(make_page(fixture_html("company_site.html"), "https://www.engro.com/"))
    assert set(info.phone_numbers) == {"+924235761234", "+923217654321", "+9221111211211"}
    assert "+923009999999" not in info.phone_numbers  # inside <script>, not visible text
    assert set(info.emails) == {"investor.relations@engro.com", "careers@engro.com", "info@engro.com"}
    assert info.social_links["facebook"] == ["https://www.facebook.com/EngroCorporation"]
    assert info.social_links["linkedin"] == ["https://www.linkedin.com/company/engro-corporation"]
    assert info.social_links["twitter"] == ["https://twitter.com/EngroCorp"]
    assert "instagram" in info.social_links and "youtube" in info.social_links
    addresses = {loc.text for loc in info.locations if loc.kind == "address"}
    assert "2nd Floor, Plot 14, Main Boulevard, Gulberg III, Lahore" in addresses
    assert any(a.startswith("8th Floor, The Harbor Front Building") for a in addresses)  # from JSON-LD


def test_metadata_extractor(parsers):
    extractor: MetadataExtractor = parsers["metadata_extractor"]
    meta = extractor.parse(make_page(fixture_html("company_site.html"), "https://www.engro.com/home"))
    assert meta.title == "Engro Corporation | Home"
    assert meta.description.startswith("Engro Corporation is one of")
    assert meta.canonical_url == "https://www.engro.com/"
    assert meta.language == "en"
    assert meta.keywords == ["fertilizer", "energy", "petrochemicals"]
    assert meta.site_name == "Engro"
    assert meta.open_graph == {"title": "Engro Corporation", "site_name": "Engro",
                               "image": "https://www.engro.com/og.png"}
    assert meta.twitter == {"card": "summary"}
    assert len(meta.json_ld) == 1  # the malformed block is skipped
    assert meta.json_ld[0]["@type"] == "Organization"


def test_html_document_text_and_links():
    doc = HtmlDocument(fixture_html("company_site.html"), "https://www.engro.com/")
    assert "tracking" not in doc.text
    assert "https://www.engro.com/about-us/" in {link.url for link in doc.links}
