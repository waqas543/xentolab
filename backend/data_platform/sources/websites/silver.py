"""Company-website Bronze -> silver.domain_facts (the shared domain-facts contract)."""

from __future__ import annotations

from ...engine.context import StageContext
from ...lake.reader import dataset_files, duck, parquet_source
from ...lake.writer import DatasetVersionWriter, copy_query_into
from ...silver_contract import DOMAIN_FACT_FIELDS, DOMAIN_FACT_METHODS

# Scraping-engine field names that the contract spells differently.
FIELD_ALIASES = {"address_locality": "city"}
# Confidence by extraction method when the engine recorded none.
METHOD_CONFIDENCE = {"site": 0.95, "jsonld": 0.9, "link": 0.8, "meta": 0.7, "text": 0.6, "llm": 0.6}


def transform(ctx: StageContext, artifact: dict, bronze: dict, transformation_version: str):
    country = artifact["country"]
    partition = {"source": "website", "country": country}
    fields = ", ".join(f"'{f}'" for f in sorted(DOMAIN_FACT_FIELDS))
    methods = ", ".join(f"'{m}'" for m in DOMAIN_FACT_METHODS)
    aliases = " ".join(f"WHEN '{k}' THEN '{v}'" for k, v in FIELD_ALIASES.items())
    confidence = " ".join(f"WHEN '{k}' THEN {v}" for k, v in METHOD_CONFIDENCE.items())
    con = duck()
    try:
        source = parquet_source(dataset_files("bronze.website_facts", versions=[bronze]))
        con.execute(f"""
            CREATE TEMP TABLE facts AS
            SELECT lower(trim(domain)) AS domain, CASE field {aliases} ELSE field END AS field,
                   regexp_replace(trim(value), '\\s+', ' ', 'g') AS value, method, evidence, evidence_url,
                   coalesce(page_kind, 'domain') AS evidence_type, confidence, extracted_at, artifact_id, bronze_id
            FROM (SELECT *, {int(bronze['id'])}::BIGINT AS bronze_id FROM {source})
        """)
        stats = dict(zip(("facts", "unknown_field", "unknown_method", "empty"), con.execute(f"""
            SELECT count(*), count(*) FILTER (WHERE field NOT IN ({fields})),
                   count(*) FILTER (WHERE method NOT IN ({methods})), count(*) FILTER (WHERE value IS NULL OR value = '')
            FROM facts
        """).fetchone()))
        with DatasetVersionWriter(ctx.storage, "silver.domain_facts", partition, None, run_id=ctx.run_id,
                                  versions={"transformation": transformation_version},
                                  inputs=[{"type": "dataset_version", "dataset_id": "bronze.website_facts",
                                           "id": bronze["id"]}]) as writer:
            copy_query_into(writer, con, f"""
                SELECT DISTINCT ON (domain, field, value, method, evidence_url)
                       domain, '{country}' AS country_code, field,
                       CASE WHEN field = 'email' THEN lower(value) ELSE value END AS value,
                       'website' AS source, method, evidence, evidence_url, evidence_type,
                       coalesce(confidence, CASE method {confidence} ELSE 0.5 END) AS confidence,
                       extracted_at::TIMESTAMPTZ AS observed_at, coalesce(evidence_url, domain) AS source_record_id,
                       artifact_id, bronze_id AS bronze_version_id, {ctx.run_id}::BIGINT AS pipeline_run_id,
                       '{transformation_version}' AS transformation_version, now()::TIMESTAMPTZ AS ingested_at
                FROM facts
                WHERE field IN ({fields}) AND method IN ({methods}) AND value IS NOT NULL AND value <> ''
                ORDER BY domain, field, value, method, evidence_url, confidence DESC NULLS LAST, extracted_at DESC
            """)
    finally:
        con.close()
    stats["written"] = writer.version["row_count"]
    return writer.version, stats
