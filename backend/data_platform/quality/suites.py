"""Quality suites for the platform-owned (cross-source) datasets."""

from .checks import (
    CoordinateCheck,
    DomainCheck,
    DuplicateRateCheck,
    NotNullCheck,
    PhoneCheck,
    RecordCountCheck,
    SchemaCheck,
    UniqueCheck,
)

SILVER_PLACES_CONTRACT = (
    ("silver_id", "VARCHAR"), ("source", "VARCHAR"), ("source_record_id", "VARCHAR"), ("name", "VARCHAR"),
    ("category_group", "VARCHAR"), ("country_code", "VARCHAR"), ("latitude", "DOUBLE"), ("longitude", "DOUBLE"),
    ("normalized_domain", "VARCHAR"), ("normalized_phone", "VARCHAR"), ("artifact_id", "BIGINT"),
    ("transformation_version", "VARCHAR"),
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
    if dataset_id == "silver.postal_codes":
        return [NotNullCheck(name="postal_code_present", column="postal_code")]
    return []


def gold_checks(dataset_id: str):
    if dataset_id == "gold.businesses":
        return [
            UniqueCheck(name="unique_gold_id", columns=("gold_id",)),
            NotNullCheck(name="name_present", column="name"),
            CoordinateCheck(name="valid_coordinates"),
            RecordCountCheck(name="row_count_stable", min_rows=1, max_change_pct=25),
            DomainCheck(name="valid_domains", column="domain", severity="warn", threshold_pct=1),
        ]
    if dataset_id == "gold.business_sources":
        return [UniqueCheck(name="one_gold_per_source_record", columns=("silver_id",)),
                NotNullCheck(name="provenance_complete", column="artifact_id")]
    if dataset_id == "gold.domains":
        return [UniqueCheck(name="unique_domain", columns=("domain",)),
                NotNullCheck(name="tld_present", column="tld")]
    if dataset_id == "gold.organizations":
        return [UniqueCheck(name="unique_organization_id", columns=("organization_id",)),
                NotNullCheck(name="name_present", column="name")]
    if dataset_id == "gold.organization_sources":
        return [UniqueCheck(name="one_organization_per_record", columns=("silver_id",))]
    if dataset_id == "gold.cities":
        return [UniqueCheck(name="unique_city_id", columns=("city_id",)),
                NotNullCheck(name="name_present", column="name")]
    return []
