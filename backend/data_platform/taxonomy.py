"""Shared category taxonomy. Every source maps its native categories onto these groups.

`classify_osm` holds the OSM mapping; other sources add their own mapping functions
that return the same (category_key, category, category_group, entity_type) shape.
"""

from __future__ import annotations

TAXONOMY_VERSION = "tax-v2"  # v2: Overture mapping (GeoNames / organization mappings are versioned by their adapters)

GROUPS = [
    "Retail", "Food & Drink", "Education", "Health", "Automotive", "Professional Services", "Finance",
    "Hospitality", "Industry & Craft", "Public & Non-profit", "Other Services",
]

OSM_CATEGORY_KEYS = ["shop", "office", "craft", "healthcare", "amenity", "tourism", "industrial", "man_made", "company"]

# Amenities that are public infrastructure rather than businesses.
OSM_EXCLUDED_AMENITIES = {
    "place_of_worship", "grave_yard", "parking", "parking_space", "parking_entrance", "drinking_water", "toilets",
    "bench", "waste_basket", "waste_disposal", "recycling", "police", "townhall", "courthouse", "fire_station",
    "shelter", "bus_station", "community_centre", "social_facility", "library", "public_bath", "fountain", "atm",
    "water_point", "watering_place", "clock", "post_box", "telephone", "prison", "ranger_station",
    "border_control", "bicycle_parking", "motorcycle_parking", "taxi", "ferry_terminal", "public_building",
    "hunting_stand", "vending_machine", "shower", "mortuary", "refugee_site", "grit_bin", "letter_box",
    "loading_dock", "bus_stop", "polling_station", "dressing_room", "give_box", "funeral_hall",
}
OSM_BUSINESS_TOURISM = {"hotel", "motel", "guest_house", "hostel", "apartment", "resort", "chalet", "camp_site"}

_AUTOMOTIVE_SHOPS = {"car", "car_repair", "car_parts", "tyres", "motorcycle", "motorcycle_repair", "truck", "auto_parts"}
_AUTOMOTIVE_AMENITIES = {"fuel", "car_wash", "car_rental", "charging_station", "vehicle_inspection", "motorcycle_rental"}
_FOOD = {"restaurant", "fast_food", "cafe", "ice_cream", "food_court", "bar", "pub", "biergarten"}
_FINANCE_AMENITIES = {"bank", "bureau_de_change", "money_transfer", "payment_centre"}
_FINANCE_OFFICES = {"financial", "insurance", "accountant", "tax_advisor", "financial_advisor"}
_HEALTH_AMENITIES = {"hospital", "clinic", "doctors", "dentist", "pharmacy", "veterinary", "nursing_home"}
_HEALTH_SHOPS = {"chemist", "medical_supply", "optician", "hearing_aids"}
_EDUCATION = {"school", "college", "university", "kindergarten", "childcare", "driving_school", "language_school",
              "training", "music_school", "prep_school"}
_PUBLIC_OFFICES = {"government", "ngo", "diplomatic", "association", "religion", "political_party", "union",
                   "foundation", "charity"}


def is_osm_business(tags: dict) -> bool:
    if tags.get("shop") == "vacant":
        return False
    if any(key in tags for key in ("shop", "office", "craft", "healthcare", "industrial", "company")):
        return True
    amenity = tags.get("amenity")
    if amenity and amenity not in OSM_EXCLUDED_AMENITIES:
        return True
    return tags.get("tourism") in OSM_BUSINESS_TOURISM or tags.get("man_made") == "works"


def classify_osm(tags: dict) -> tuple[str | None, str | None, str, str]:
    """(category_key, category, category_group, entity_type) for an OSM tag set."""
    key = next((k for k in OSM_CATEGORY_KEYS if k in tags), None)
    value = tags.get(key) if key else None
    category = f"unspecified_{key}" if value == "yes" else value

    shop, amenity, office = tags.get("shop"), tags.get("amenity"), tags.get("office")
    if shop in _AUTOMOTIVE_SHOPS or amenity in _AUTOMOTIVE_AMENITIES:
        group = "Automotive"
    elif amenity in _FOOD:
        group = "Food & Drink"
    elif amenity in _FINANCE_AMENITIES or office in _FINANCE_OFFICES:
        group = "Finance"
    elif "healthcare" in tags or amenity in _HEALTH_AMENITIES or shop in _HEALTH_SHOPS:
        group = "Health"
    elif amenity in _EDUCATION or office == "educational_institution":
        group = "Education"
    elif "tourism" in tags:
        group = "Hospitality"
    elif office in _PUBLIC_OFFICES:
        group = "Public & Non-profit"
    elif office:
        group = "Professional Services"
    elif shop:
        group = "Retail"
    elif "craft" in tags or "industrial" in tags or tags.get("man_made") == "works":
        group = "Industry & Craft"
    else:
        group = "Other Services"

    entity_type = "organization" if group == "Public & Non-profit" else "business"
    return key, category, group, entity_type


# ---------------------------------------------------------------- Overture
# Overture's taxonomy: hierarchy like ["food_and_drink", "restaurant", "pakistani_restaurant"].
# Excluded = not businesses, mirroring the OSM rules (worship, public safety, parks, transport hubs, nature).

OVERTURE_EXCLUDED_TOP = {"geographic_entities", "cultural_and_historic"}
OVERTURE_EXCLUDED_SECOND = {
    "public_safety_service", "military_site", "public_utility", "public_facility",          # community_and_government
    "ground_transport_facility_or_service", "air_transport_facility_or_service", "parking",  # travel_and_transportation
    "park", "recreational_trail_or_path",                                                    # sports_and_recreation
}

_OVERTURE_GROUPS_BY_TOP = {
    "food_and_drink": "Food & Drink",
    "shopping": "Retail",
    "health_care": "Health",
    "education": "Education",
    "lodging": "Hospitality",
    "community_and_government": "Public & Non-profit",
    "lifestyle_services": "Other Services",
    "sports_and_recreation": "Other Services",
    "arts_and_entertainment": "Other Services",
}
_OVERTURE_GROUPS_BY_SECOND = {
    "financial_service": "Finance",
    "vehicle_service": "Automotive",
    "fueling_station": "Automotive",
    "travel_service": "Professional Services",
    "building_or_construction_service": "Industry & Craft",
    "home_service": "Industry & Craft",
    "agricultural_service": "Industry & Craft",
}
_OVERTURE_HEALTH_PRIMARY = {"pharmacy", "drugstore", "optician", "medical_supply_store"}
_OVERTURE_AUTOMOTIVE_PRIMARY = {"car_dealer", "auto_parts_store", "motorcycle_dealer", "tire_shop", "gas_station"}


def classify_overture(hierarchy: list[str] | None, primary: str | None) -> tuple[str | None, str | None, str, str] | None:
    """(category_key, category, category_group, entity_type), or None when the place is not a business."""
    hierarchy = [h for h in (hierarchy or []) if h]
    top = hierarchy[0] if hierarchy else None
    second = hierarchy[1] if len(hierarchy) > 1 else None
    category = primary or (hierarchy[-1] if hierarchy else None)
    if top in OVERTURE_EXCLUDED_TOP or second in OVERTURE_EXCLUDED_SECOND:
        return None
    if category in _OVERTURE_HEALTH_PRIMARY:
        group = "Health"
    elif category in _OVERTURE_AUTOMOTIVE_PRIMARY:
        group = "Automotive"
    elif second in _OVERTURE_GROUPS_BY_SECOND:
        group = _OVERTURE_GROUPS_BY_SECOND[second]
    elif top == "services_and_business":
        group = "Professional Services"
    else:
        group = _OVERTURE_GROUPS_BY_TOP.get(top, "Other Services")
    entity_type = "organization" if group == "Public & Non-profit" else "business"
    return top, category, group, entity_type


# ---------------------------------------------------------------- GeoNames
# Spot features (class S) that are businesses or institutions. Everything else in GeoNames is geography.

GEONAMES_FEATURES: dict[str, tuple[str, str, str]] = {   # code -> (category, group, entity_type)
    "HTL": ("hotel", "Hospitality", "business"), "MOTL": ("motel", "Hospitality", "business"),
    "RSRT": ("resort", "Hospitality", "business"), "REST": ("restaurant", "Food & Drink", "business"),
    "BANK": ("bank", "Finance", "business"),
    "HSP": ("hospital", "Health", "business"), "HSPC": ("clinic", "Health", "business"),
    "HSPD": ("dispensary", "Health", "business"), "HSPL": ("leprosarium", "Health", "business"),
    "SCH": ("school", "Education", "business"), "SCHC": ("college", "Education", "business"),
    "SCHM": ("military_school", "Education", "organization"), "SCHT": ("technical_school", "Education", "business"),
    "SCHA": ("agricultural_school", "Education", "business"), "SCHN": ("maritime_school", "Education", "business"),
    "UNIV": ("university", "Education", "business"),
    "MALL": ("mall", "Retail", "business"), "MKT": ("market", "Retail", "business"),
    "BLDO": ("office_building", "Professional Services", "business"),
    "MFG": ("factory", "Industry & Craft", "business"), "MFGB": ("brewery", "Industry & Craft", "business"),
    "MFGC": ("cannery", "Industry & Craft", "business"), "MFGCU": ("copper_works", "Industry & Craft", "business"),
    "MFGM": ("munitions_plant", "Industry & Craft", "business"),
    "MFGPH": ("phosphate_works", "Industry & Craft", "business"),
    "MFGQ": ("abandoned_factory", "Industry & Craft", "business"),
    "MFGSG": ("sugar_refinery", "Industry & Craft", "business"),
    "ML": ("mill", "Industry & Craft", "business"), "MLSG": ("sugar_mill", "Industry & Craft", "business"),
    "MLSGQ": ("former_sugar_mill", "Industry & Craft", "business"), "MLSW": ("sawmill", "Industry & Craft", "business"),
    "MLFLX": ("flax_mill", "Industry & Craft", "business"), "MLM": ("ore_mill", "Industry & Craft", "business"),
    "MLO": ("olive_oil_mill", "Industry & Craft", "business"), "MLWND": ("windmill", "Industry & Craft", "business"),
    "MLWTR": ("water_mill", "Industry & Craft", "business"), "CMPMN": ("mining_camp", "Industry & Craft", "business"),
    "MN": ("mine", "Industry & Craft", "business"), "QRY": ("quarry", "Industry & Craft", "business"),
    "OILR": ("oil_refinery", "Industry & Craft", "business"), "PS": ("power_station", "Industry & Craft", "business"),
    "PSH": ("hydroelectric_station", "Industry & Craft", "business"),
    "GOSP": ("gas_oil_separator", "Industry & Craft", "business"),
    "DIP": ("diplomatic_facility", "Public & Non-profit", "organization"),
    "ADMF": ("government_office", "Public & Non-profit", "organization"),
    "PO": ("post_office", "Public & Non-profit", "organization"),
    "CTRR": ("religious_center", "Public & Non-profit", "organization"),
    "INSM": ("military_installation", "Public & Non-profit", "organization"),
    "AIRP": ("airport", "Professional Services", "business"),
    "RSTN": ("railroad_station", "Professional Services", "business"),
    "STNB": ("scientific_station", "Professional Services", "organization"),
    "MUS": ("museum", "Other Services", "organization"), "THTR": ("theater", "Other Services", "business"),
    "STDM": ("stadium", "Other Services", "organization"), "ZOO": ("zoo", "Other Services", "organization"),
}
# Only stations of interest to a business directory: ordinary railway stops and military sites are geography.
GEONAMES_EXCLUDED_BY_DEFAULT = {"RSTN", "INSM", "MFGQ", "MLSGQ"}


def classify_geonames(feature_class: str | None, feature_code: str | None) \
        -> tuple[str | None, str | None, str, str] | None:
    """(category_key, category, category_group, entity_type) for a GeoNames spot feature, or None."""
    if feature_class != "S" or feature_code not in GEONAMES_FEATURES or feature_code in GEONAMES_EXCLUDED_BY_DEFAULT:
        return None
    category, group, entity_type = GEONAMES_FEATURES[feature_code]
    return feature_code, category, group, entity_type


# ---------------------------------------------------------------- organizations (Wikidata classes, registry types)
# Keyword rules over the organization's type and industry labels, first match wins.

_ORGANIZATION_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("Education", ("school", "university", "college", "academy", "educational", "seminary", "madrasa", "institute of")),
    ("Health", ("hospital", "clinic", "medical", "health", "pharmac", "dental", "laborator")),
    ("Finance", ("bank", "financ", "insurance", "investment", "stock exchange", "microfinance", "leasing",
                 "asset management", "modaraba", "mutual fund")),
    ("Hospitality", ("hotel", "resort", "hospitality", "lodging")),
    ("Food & Drink", ("restaurant", "food", "beverage", "bakery", "dairy", "brewery", "fast food", "coffee")),
    ("Retail", ("retail", "store", "supermarket", "shop", "department store", "e-commerce", "brand")),
    ("Automotive", ("automo", "car ", "motor vehicle", "motorcycle", "vehicle manufacturer")),
    ("Public & Non-profit", ("government", "ministry", "agency", "political party", "military", "armed force",
                             "embassy", "consulate", "non-profit", "nonprofit", "non-governmental", "ngo",
                             "charit", "foundation", "association", "trade union", "tribe", "court", "legislature",
                             "assembly", "municipal", "religious", "mosque", "police", "council", "commission",
                             "authority", "regulator", "federation", "sports team", "cricket team", "club")),
    ("Industry & Craft", ("manufactur", "industr", "textile", "cement", "steel", "construction", "engineering",
                          "chemical", "fertili", "energy", "oil", "gas", "petroleum", "mining", "power",
                          "electric utility", "sugar", "agricultur", "conglomerate", "factory")),
    ("Professional Services", ("software", "technology", "information technology", "telecom", "media",
                               "consult", "law firm", "airline", "logistics", "transport", "publish",
                               "television", "radio", "newspaper", "broadcast", "advertis", "real estate",
                               "company", "enterprise", "business", "corporation", "holding")),
]


def classify_organization(labels: list[str] | None) -> str:
    """Category group for an organization from its type / industry labels (e.g. Wikidata P31 + P452)."""
    text = " " + " | ".join(label.lower() for label in labels or [] if label) + " "
    for group, keywords in _ORGANIZATION_RULES:
        if any(keyword in text for keyword in keywords):
            return group
    return "Other Services"
