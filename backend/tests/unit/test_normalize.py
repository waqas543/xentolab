import pytest

from data_platform.normalize import (
    name_key,
    normalize_email,
    normalize_name,
    normalize_phone,
    normalize_website,
    valid_coordinates,
)
from data_platform.taxonomy import classify_osm, is_osm_business


@pytest.mark.parametrize("raw, expected", [
    ("Systems (Pvt.) Limited", "systems"),
    ("SYSTEMS LIMITED", "systems"),
    ("Systems Ltd.", "systems"),
    ("The Coffee Co.", "coffee"),
    ("Ltd", "ltd"),                     # never reduce a name to nothing
])
def test_name_key_strips_legal_forms(raw, expected):
    assert name_key(raw) == expected


def test_normalize_name_handles_unicode_and_case():
    assert normalize_name("  ＡＢＣ   Bakers!! ") == "abc bakers"
    assert normalize_name("الائیڈ بینک") == "الائیڈ بینک"
    assert normalize_name("") is None


@pytest.mark.parametrize("raw, domain, tld, platform", [
    ("https://www.systems-ltd.com/careers", "systems-ltd.com", "com", False),
    ("www.shop.example.com.pk", "example.com.pk", "pk", False),
    ("HTTP://Bitzage.com/", "bitzage.com", "com", False),
    ("https://www.facebook.com/somepage", "facebook.com", "com", True),
    ("site.one.org; site.two.org", "one.org", "org", False),
    ("http://xn--mnchen-3ya.de", "xn--mnchen-3ya.de", "de", False),
    ("münchen.de", "xn--mnchen-3ya.de", "de", False),
])
def test_normalize_website(raw, domain, tld, platform):
    url, got_domain, got_tld, is_platform = normalize_website(raw)
    assert (got_domain, got_tld, is_platform) == (domain, tld, platform)
    assert url.startswith("http")


@pytest.mark.parametrize("raw", [None, "", "not a url", "info@shop.com", "http://localhost", "http://bad"])
def test_invalid_websites(raw):
    assert normalize_website(raw)[1] is None


@pytest.mark.parametrize("raw, country, expected", [
    ("042 35781769", "PK", "+924235781769"),
    ("+92 300 1234567", None, "+923001234567"),
    ("03001234567;042111945227", "PK", "+923001234567"),
    ("0092 42 111 945 227", "PK", "+9242111945227"),
    ("12345", "PK", None),
    ("call us", "PK", None),
])
def test_normalize_phone(raw, country, expected):
    assert normalize_phone(raw, country) == expected


def test_normalize_email():
    assert normalize_email("Mailto:Info@Shop.COM") == "info@shop.com"
    assert normalize_email("not-an-email") is None


def test_coordinates():
    assert valid_coordinates(31.5, 74.3)
    assert not valid_coordinates(0, 0)
    assert not valid_coordinates(95, 10)
    assert not valid_coordinates(None, 10)


@pytest.mark.parametrize("tags, group, category", [
    ({"amenity": "restaurant", "name": "X"}, "Food & Drink", "restaurant"),
    ({"shop": "car_repair"}, "Automotive", "car_repair"),
    ({"office": "government"}, "Public & Non-profit", "government"),
    ({"shop": "yes"}, "Retail", "unspecified_shop"),
    ({"amenity": "bank"}, "Finance", "bank"),
    ({"tourism": "hotel"}, "Hospitality", "hotel"),
    ({"craft": "tailor"}, "Industry & Craft", "tailor"),
])
def test_classify_osm(tags, group, category):
    _, got_category, got_group, _ = classify_osm(tags)
    assert (got_group, got_category) == (group, category)


def test_business_filter():
    assert is_osm_business({"shop": "bakery"})
    assert is_osm_business({"amenity": "cafe"})
    assert not is_osm_business({"amenity": "place_of_worship"})
    assert not is_osm_business({"shop": "vacant"})
    assert not is_osm_business({"highway": "residential"})
    assert is_osm_business({"tourism": "hotel"}) and not is_osm_business({"tourism": "viewpoint"})


def test_webmail_shorteners_and_site_builders_are_platforms():
    from data_platform.normalize import normalize_website

    for url in ["someone@gmail.com", "https://bit.ly/3abc", "http://en.wikipedia.org/wiki/HBL", "https://wix.com/x",
                "https://g.page/r/abc", "https://forms.gle/xyz"]:
        _, domain, _, platform = normalize_website(url)
        assert domain is None or platform, url


def test_private_suffixes_make_hosted_sites_their_own_domain():
    from data_platform.normalize import normalize_website, registered_domain

    assert normalize_website("https://kababjees.myshopify.com/")[1:] == ("kababjees.myshopify.com", "com", False)
    assert registered_domain("shop.example.blogspot.com")[0] == "example.blogspot.com"
    assert registered_domain("www.systems-ltd.com") == ("systems-ltd.com", "com")
