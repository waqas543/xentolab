"""ANALYTICS_REFRESH: load the current Gold partition into the PostgreSQL serving tables.

Each table partition (country) is replaced in one serving-database transaction, so
dashboards never see a half-loaded country. dp.serving_state records which Gold
version is live; publishing the same version again is a no-op.
"""

from __future__ import annotations

import pyarrow.parquet as pq

from .. import db, repository
from ..engine.context import StageContext
from ..lake.reader import version_files
from ..sources.base import StageResult

SERVING_TABLES = {
    "gold.businesses": ("gold.businesses", [
        "gold_id", "name", "normalized_name", "entity_type", "category_group", "category", "country_code", "admin1",
        "admin2", "city", "locality", "postal_code", "address", "latitude", "longitude", "website", "domain", "tld",
        "phone", "email", "brand_name", "brand_wikidata", "organization_id", "sources", "source_count",
        "member_count", "has_website", "has_phone", "has_email", "has_address", "completeness", "conflicts",
        "source_updated_at", "built_at", "field_sources", "source_confidence", "wikidata_id", "enriched_by"]),
    "gold.business_sources": ("gold.business_sources", [
        "gold_id", "silver_id", "country_code", "source", "source_record_id", "artifact_id", "silver_version_id",
        "pipeline_run_id", "transformation_version", "match_confidence", "is_primary"]),
    "gold.organizations": ("gold.organizations", [
        "organization_id", "country_code", "name", "brand_wikidata", "domain", "category_group", "location_count",
        "city_count", "sources", "legal_name", "wikidata_id", "website", "description", "industry", "headquarters",
        "inception", "employees", "parent_name", "registry_id", "jurisdiction", "registry_status", "identifiers",
        "aliases", "linked_records", "news_mentions", "news_articles", "news_last_seen", "news_tone",
        "field_sources"]),
    "gold.organization_sources": ("gold.organization_sources", [
        "organization_id", "silver_id", "country_code", "source", "source_record_id", "link_method", "confidence",
        "artifact_id", "pipeline_run_id", "transformation_version", "source_url"]),
    "gold.domains": ("gold.domains", [
        "domain", "country_code", "tld", "business_count", "organization_count", "sources", "top_category_group",
        "organization_id", "domain_type", "is_business_candidate", "crawl_pages", "crawl_captures", "crawl_hosts",
        "crawl_first_seen", "crawl_last_seen", "crawl_languages", "homepage_url", "homepage_status", "crawl"]),
    "gold.cities": ("gold.cities", [
        "city_id", "country_code", "name", "name_local", "kind", "admin1", "admin2", "latitude", "longitude",
        "population", "business_count", "with_website", "with_phone", "organizations", "businesses_per_10k",
        "top_category_group", "source"]),
}
# Provenance tables are rebuilt with their Gold version but carry no version column.
WITHOUT_VERSION_COLUMN = {"gold.business_sources", "gold.organization_sources"}


def publish(ctx: StageContext) -> StageResult:
    country = ctx.params["country"]
    partition_key = repository.partition_key({"country": country})
    published = 0
    skipped = []

    for dataset_id, (table, columns) in SERVING_TABLES.items():
        versions = repository.current_versions(dataset_id, {"country": country})
        if not versions:
            continue
        version = versions[0]
        state = repository.get_serving_state(dataset_id, partition_key)
        if state and state["dataset_version_id"] == version["id"]:
            skipped.append(dataset_id)
            continue

        rows = 0
        with db.serving() as conn, conn.transaction():
            conn.execute(f"DELETE FROM {table} WHERE country_code = %s", [country])
            copy_columns = columns + (["gold_version_id"] if dataset_id not in WITHOUT_VERSION_COLUMN else [])
            with conn.cursor() as cur, cur.copy(f"COPY {table} ({', '.join(copy_columns)}) FROM STDIN") as copy:
                for path in version_files(version):
                    for batch in pq.ParquetFile(path).iter_batches(batch_size=20_000, columns=columns):
                        data = batch.to_pydict()
                        for i in range(batch.num_rows):
                            values = [data[c][i] for c in columns]
                            if dataset_id not in WITHOUT_VERSION_COLUMN:
                                values.append(version["id"])
                            copy.write_row(values)
                        rows += batch.num_rows
                        ctx.progress(published + rows, None, "rows", f"publishing {table}")
        repository.set_serving_state(dataset_id, partition_key, version["id"], rows, ctx.run_id)
        published += rows

    if not published:
        return StageResult.skipped("Serving tables already hold the current Gold versions")
    return StageResult(records_out=published, metrics={"published_rows": published, "unchanged": skipped})
