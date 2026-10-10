"""Datasets owned by the platform itself (cross-source Silver contract, Gold)."""

from .. import repository

PLATFORM_DATASETS = [
    dict(dataset_id="silver.places", layer="silver", name="Places",
         description="Normalized place/business observations from every source (one row per source record).",
         source_id=None, partition_columns=["source", "country"]),
    dict(dataset_id="silver.rejects", layer="silver", name="Rejected records",
         description="Source records that could not be normalized, with the reason.",
         source_id=None, partition_columns=["source", "country"]),
    dict(dataset_id="silver.geo_boundaries", layer="silver", name="Administrative boundaries",
         description="Country / province / district polygons (WKB).",
         source_id=None, partition_columns=["source", "country"]),
    dict(dataset_id="silver.geo_places", layer="silver", name="Populated places",
         description="Cities, towns and villages (with population) used to assign city / locality.",
         source_id=None, partition_columns=["source", "country"]),
    dict(dataset_id="silver.postal_codes", layer="silver", name="Postal codes",
         description="Postal codes with place and administrative names.",
         source_id=None, partition_columns=["source", "country"]),
    dict(dataset_id="silver.organizations", layer="silver", name="Organizations (source records)",
         description="Companies, brands and institutions from knowledge bases and registries (one row per record).",
         source_id=None, partition_columns=["source", "country"]),
    dict(dataset_id="silver.web_domains", layer="silver", name="Web domains (crawl)",
         description="Registrable domains discovered in web-crawl indexes, with crawl statistics.",
         source_id=None, partition_columns=["source", "country"]),
    dict(dataset_id="silver.org_mentions", layer="silver", name="Organization news mentions",
         description="Organizations named in news coverage of the country, aggregated per normalized name.",
         source_id=None, partition_columns=["source", "country"]),
    dict(dataset_id="gold.businesses", layer="gold", name="Businesses",
         description="Resolved real-world business locations with survivorship-merged attributes.",
         source_id=None, partition_columns=["country"], serving_table="gold.businesses"),
    dict(dataset_id="gold.business_sources", layer="gold", name="Business sources (provenance)",
         description="Which source records make up each Gold business.",
         source_id=None, partition_columns=["country"], serving_table="gold.business_sources"),
    dict(dataset_id="gold.organizations", layer="gold", name="Organizations",
         description="Companies / brands operating one or more business locations.",
         source_id=None, partition_columns=["country"], serving_table="gold.organizations"),
    dict(dataset_id="gold.organization_sources", layer="gold", name="Organization sources (provenance)",
         description="Which source records (registries, knowledge bases, brands) make up each organization.",
         source_id=None, partition_columns=["country"], serving_table="gold.organization_sources"),
    dict(dataset_id="gold.domains", layer="gold", name="Domains",
         description="Registrable web domains used by businesses or discovered by crawls.",
         source_id=None, partition_columns=["country"], serving_table="gold.domains"),
    dict(dataset_id="gold.cities", layer="gold", name="Cities",
         description="Cities and towns with population and business coverage.",
         source_id=None, partition_columns=["country"], serving_table="gold.cities"),
]


def register_platform_datasets() -> None:
    for dataset in PLATFORM_DATASETS:
        repository.register_dataset(**dataset)
