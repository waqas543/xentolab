"""Quality suites for the platform-owned (cross-source) datasets."""

import re

from ..silver_contract import DOMAIN_FACT_FIELDS, DOMAIN_FACT_METHODS
from ..categories import CATEGORIES
from .checks import (
    BlankStringCheck,
    CoordinateCheck,
    DomainCheck,
    DuplicateRateCheck,
    ExpressionCheck,
    NotNullCheck,
    PhoneCheck,
    RecordCountCheck,
    RegexCheck,
    SchemaCheck,
    UniqueCheck,
)

SILVER_PLACES_CONTRACT = (
    ("silver_id", "VARCHAR"), ("source", "VARCHAR"), ("source_record_id", "VARCHAR"), ("name", "VARCHAR"),
    ("category_group", "VARCHAR"), ("country_code", "VARCHAR"), ("latitude", "DOUBLE"), ("longitude", "DOUBLE"),
    ("normalized_domain", "VARCHAR"), ("normalized_phone", "VARCHAR"), ("artifact_id", "BIGINT"),
    ("transformation_version", "VARCHAR"),
)

SILVER_DOMAIN_FACTS_CONTRACT = (
    ("domain", "VARCHAR"), ("country_code", "VARCHAR"), ("field", "VARCHAR"), ("value", "VARCHAR"),
    ("source", "VARCHAR"), ("method", "VARCHAR"), ("evidence_type", "VARCHAR"), ("transformation_version", "VARCHAR"),
)


def silver_checks(dataset_id: str):
    if dataset_id == "silver.places":
        return [
            SchemaCheck(name="silver_contract", required=SILVER_PLACES_CONTRACT),
            UniqueCheck(name="unique_silver_id", columns=("silver_id",)),
            CoordinateCheck(name="valid_coordinates"),
            NotNullCheck(name="category_present", column="category_group"),
            RecordCountCheck(name="row_count_stable", min_rows=1, max_change_pct=25),
            # Informational coverage checks: recorded for the quality dashboard, never blocking.
            NotNullCheck(name="name_present", column="name", severity="warn", threshold_pct=60),
            NotNullCheck(name="country_assigned", column="country_code", severity="warn", threshold_pct=5),
            DomainCheck(name="valid_domains", severity="warn", threshold_pct=1),
            PhoneCheck(name="phones_normalizable", severity="warn", threshold_pct=30),
            DuplicateRateCheck(name="duplicate_names_nearby", columns=("name_key", "city"), severity="warn",
                               threshold_pct=40, where="has_name"),
            # Data quality (silver-contract-3): tracked on the dashboard so regressions show.
            ExpressionCheck(name="approximate_locations", failing="location_precision = 'approximate'",
                            description="placed on a town centre or stacked with many other records",
                            severity="warn", threshold_pct=10),
            ExpressionCheck(name="city_assigned", failing="city_id IS NULL", where="location_precision = 'exact'",
                            description="an exactly placed record without a gazetteer city", severity="warn",
                            threshold_pct=5),
        ]
    if dataset_id == "silver.geo_boundaries":
        return [RecordCountCheck(name="boundaries_present", min_rows=1, severity="warn"),
                UniqueCheck(name="unique_boundary", columns=("boundary_id",))]
    if dataset_id == "silver.geo_places":
        return [UniqueCheck(name="unique_place", columns=("place_id",)),
                CoordinateCheck(name="valid_coordinates"),
                NotNullCheck(name="name_present", column="name"),
                RecordCountCheck(name="row_count_stable", min_rows=1, max_change_pct=25)]
    if dataset_id == "silver.organizations":
        return [UniqueCheck(name="unique_silver_id", columns=("silver_id",)),
                NotNullCheck(name="name_present", column="name"),
                NotNullCheck(name="name_key_present", column="name_key", severity="warn", threshold_pct=2),
                DomainCheck(name="valid_domains", severity="warn", threshold_pct=1),
                RecordCountCheck(name="row_count_stable", min_rows=0, max_change_pct=50, severity="warn")]
    if dataset_id == "silver.web_domains":
        return [UniqueCheck(name="unique_domain", columns=("domain",)),
                DomainCheck(name="valid_domains", column="domain", severity="warn", threshold_pct=1),
                RecordCountCheck(name="row_count_stable", min_rows=1, max_change_pct=50, severity="warn")]
    if dataset_id == "silver.org_mentions":
        return [UniqueCheck(name="unique_name_key", columns=("name_key",)),
                NotNullCheck(name="name_present", column="name")]
    if dataset_id == "silver.domain_facts":
        # The shared vocabulary is what lets Gold merge any source: an unknown field or method is a contract breach.
        return [SchemaCheck(name="silver_contract", required=SILVER_DOMAIN_FACTS_CONTRACT),
                RegexCheck(name="known_field", column="field", pattern="|".join(re.escape(f) for f in sorted(DOMAIN_FACT_FIELDS))),
                RegexCheck(name="known_method", column="method", pattern="|".join(DOMAIN_FACT_METHODS)),
                NotNullCheck(name="value_present", column="value"),
                DomainCheck(name="valid_domains", column="domain", severity="warn", threshold_pct=1),
                RecordCountCheck(name="row_count_stable", min_rows=1, max_change_pct=50, severity="warn")]
    if dataset_id == "silver.postal_codes":
        return [NotNullCheck(name="postal_code_present", column="postal_code")]
    return []


GENERIC_CATEGORIES = "(" + ", ".join(f"'{c.key}'" for c in CATEGORIES.values() if c.generic) + ")"


def gold_checks(dataset_id: str):
    if dataset_id == "gold.businesses":
        return [
            UniqueCheck(name="unique_gold_id", columns=("gold_id",)),
            NotNullCheck(name="name_present", column="name"),
            CoordinateCheck(name="valid_coordinates"),
            RecordCountCheck(name="row_count_stable", min_rows=1, max_change_pct=25),
            DomainCheck(name="valid_domains", column="domain", severity="warn", threshold_pct=1),
            BlankStringCheck(name="no_blank_strings", severity="warn"),
            NotNullCheck(name="category_present", column="category"),
            ExpressionCheck(name="generic_category", failing=f"category IN {GENERIC_CATEGORIES}",
                            description="only a catch-all category (Shop, Company office, Other services)",
                            severity="warn", threshold_pct=5),
            ExpressionCheck(name="approximate_locations", failing="location_precision = 'approximate'",
                            description="no member is placed exactly", severity="warn", threshold_pct=5),
            ExpressionCheck(name="city_assigned", failing="city_id IS NULL", where="location_precision = 'exact'",
                            severity="warn", threshold_pct=2),
            ExpressionCheck(name="phone_in_name", failing="regexp_matches(name, '\\d{7,}')",
                            description="a phone number left in the display name", severity="warn",
                            threshold_pct=0.1),
            ExpressionCheck(name="platform_as_website", failing="website IS NOT NULL AND social_url = website",
                            description="a social / link page counted as the business's website", severity="warn",
                            threshold_pct=0),
            ExpressionCheck(name="shared_phones", failing="phone_kind IN ('shared', 'hotline')",
                            description="a head-office or shared number instead of the location's own",
                            severity="warn", threshold_pct=5),
        ]
    if dataset_id == "gold.business_sources":
        return [UniqueCheck(name="one_gold_per_source_record", columns=("silver_id",)),
                NotNullCheck(name="provenance_complete", column="artifact_id")]
    if dataset_id == "gold.domains":
        return [UniqueCheck(name="unique_domain", columns=("domain",)),
                NotNullCheck(name="tld_present", column="tld")]
    if dataset_id == "gold.domain_profiles":
        return [UniqueCheck(name="unique_domain", columns=("domain",)),
                NotNullCheck(name="has_sources", column="field_sources")]
    if dataset_id == "gold.organizations":
        return [UniqueCheck(name="unique_organization_id", columns=("organization_id",)),
                NotNullCheck(name="name_present", column="name"),
                BlankStringCheck(name="no_blank_strings", severity="warn"),
                ExpressionCheck(name="chain_named_after_branch",
                                failing="location_count >= 3 AND regexp_matches(lower(name), '\\b(branch|campus)\\b')",
                                description="a chain named after one of its branches", severity="warn",
                                threshold_pct=0.5),
                ExpressionCheck(name="records_without_locations", failing="location_count = 0",
                                description="known only from a registry / knowledge base", severity="warn",
                                threshold_pct=25)]
    if dataset_id == "gold.organization_sources":
        return [UniqueCheck(name="one_organization_per_record", columns=("silver_id",))]
    if dataset_id == "gold.cities":
        return [UniqueCheck(name="unique_city_id", columns=("city_id",)),
                NotNullCheck(name="name_present", column="name")]
    return []
