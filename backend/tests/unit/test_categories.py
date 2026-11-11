"""The canonical category list: every source label maps to one category in one group."""

from data_platform.categories import CATEGORIES, canonical_category, classify_label, is_generic_name


def key(category, name=None, group=None, evidence=True):
    result = canonical_category(category, None, group, name, evidence)
    return result[0] if result else None


def test_same_meaning_from_every_source_is_one_category():
    for label in ("school", "high_school", "School", "Girls' high school", "Government school"):
        assert key(label) == "school", label
    for label in ("fast_food_restaurant", "fast_food", "Fast food restaurant", "pizza_restaurant"):
        assert key(label) == "fast_food", label
    assert key("bank_or_credit_union") == key("bank") == key("Bank") == "bank"


def test_specific_rules_win_over_broad_ones():
    assert key("Post office") == "post_office"                  # not a company "office"
    assert key("Barber shop") == "barber"                       # not a generic "shop"
    assert key("Dental clinic") == "dentist"
    assert key("Animal hospital") == "veterinary"
    assert key("Mobile phone repair shop") == "repair_services"
    assert key("Industrial real estate agency") == "real_estate"
    assert key("Non-governmental organization") == "ngo_charity"


def test_each_category_has_one_group():
    groups = {}
    for category in CATEGORIES.values():
        assert groups.setdefault(category.key, category.group) == category.group


def test_non_businesses_are_excluded_and_names_refine_or_override():
    assert key("Mountain peak") is None and key("library") is None
    assert key("amusement_park") == "entertainment"             # a park that is a business
    assert key("Nightclub", "Aagosh Garden Marriage Club") == "event_venue"
    assert key("unspecified_shop", "Al-Shifa Medical Store") == "pharmacy"
    assert key(None, "Daastan Clothing") == "clothing"           # no label: the name's words
    assert key(None, "Chak No 52", evidence=False) is None       # a place name, not a business
    assert key("shopping", "Faisal Masjid") is None              # a mosque filed as shopping


def test_unknown_labels_fall_back_to_their_group():
    assert key("Something new", group="Health") == "other_health"
    assert classify_label("Faisalabad") is None                  # a scraper glitch, not a category


def test_generic_names():
    assert is_generic_name("Brick Kiln") and is_generic_name("Medical Store")
    assert not is_generic_name("Al-Shifa Medical Store")
