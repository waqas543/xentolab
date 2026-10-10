"""OpenCorporates Bronze -> silver.organizations (latest record per registry company)."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from ...engine.context import StageContext
from ...lake.reader import dataset_files, duck, parquet_source
from ...lake.writer import DatasetVersionWriter
from ...normalize import name_key, normalize_name
from ...silver_contract import ORGANIZATIONS_SCHEMA, empty_columns, to_table
from ...taxonomy import classify_organization


def display_name(name: str | None) -> str | None:
    """Registries often shout ('SYSTEMS LIMITED'); keep the legal spelling separately and title-case for display."""
    if not name:
        return None
    return name.title() if name.isupper() else name


def transform(ctx: StageContext, country: str, bronze: list[dict], transformation_version: str):
    partition = {"source": "opencorporates", "country": country}
    con = duck()
    try:
        rows = con.execute(f"""
            SELECT DISTINCT ON (jurisdiction_code, company_number) *
            FROM {parquet_source(dataset_files('bronze.opencorporates_companies', versions=bronze))}
            WHERE company_number IS NOT NULL
            ORDER BY jurisdiction_code, company_number, searched_at DESC, result_rank
        """).arrow().read_all().to_pylist()
    finally:
        con.close()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    columns = empty_columns(ORGANIZATIONS_SCHEMA)
    for row in rows:
        company = json.loads(row["company_json"])
        registry_id = f"{row['jurisdiction_code']}/{row['company_number']}"
        key = name_key(row["name"])
        previous = [p.get("company_name") for p in company.get("previous_names") or [] if p.get("company_name")]
        industries = [c.get("industry_code", {}).get("description") for c in company.get("industry_codes") or []]
        industries = [i for i in industries if i]
        values = {
            "silver_id": f"opencorporates:{registry_id}", "source": "opencorporates", "source_record_id": registry_id,
            "name": display_name(row["name"]), "legal_name": row["name"],
            "normalized_name": normalize_name(row["name"]), "name_key": key,
            "aliases": previous or None, "alias_keys": sorted({name_key(p) for p in previous} - {key, None}) or None,
            "entity_type": "company", "org_types": [row["company_type"]] if row["company_type"] else None,
            "industries": industries or None,
            "category_group": classify_organization(([row["company_type"]] if row["company_type"] else []) + industries),
            "country_code": country, "jurisdiction": row["jurisdiction_code"],
            "address": row["registered_address"],
            "inception": row["incorporation_date"], "dissolved": row["dissolution_date"],
            "status": row["current_status"] or ("Dissolved" if row["dissolution_date"] else None),
            "legal_form": row["company_type"], "registry_id": registry_id,
            "identifiers": json.dumps({"opencorporates": registry_id}),
            "source_url": row["opencorporates_url"],
            # The search matched the name we asked for exactly (normalized) or only partially.
            "source_confidence": 1.0 if key and key == row["query_name_key"] else 0.5,
            "source_updated_at": row["searched_at"], "source_version": row["searched_at"].date().isoformat(),
            "artifact_id": row["artifact_id"], "bronze_version_id": None, "pipeline_run_id": ctx.run_id,
            "transformation_version": transformation_version, "ingested_at": now,
            "attributes": json.dumps({"query": row["query"], "query_origin": row["query_origin"],
                                      "registry_url": row["registry_url"],
                                      "source": company.get("source")}, ensure_ascii=False, default=str),
        }
        for column in columns:
            columns[column].append(values.get(column))
    with DatasetVersionWriter(ctx.storage, "silver.organizations", partition, ORGANIZATIONS_SCHEMA, run_id=ctx.run_id,
                              versions={"transformation": transformation_version},
                              inputs=[{"type": "dataset_version", "dataset_id": "bronze.opencorporates_companies",
                                       "id": v["id"]} for v in bronze]) as writer:
        writer.write_batch(to_table(columns, ORGANIZATIONS_SCHEMA))
    exact = sum(1 for v in columns["source_confidence"] if v == 1.0)
    return writer.version, {"records": len(rows), "exact_name_matches": exact}
