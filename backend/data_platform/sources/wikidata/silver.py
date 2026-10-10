"""Wikidata Bronze -> silver.organizations.

Claims are read from the preserved entity JSON; item-valued properties (types, industries,
headquarters, parents...) are shown by label, using the labels fetched alongside.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pyarrow.parquet as pq

from ...engine.context import StageContext
from ...lake.writer import DatasetVersionWriter
from ...normalize import name_key, normalize_email, normalize_name, normalize_phone, normalize_website
from ...silver_contract import ORGANIZATIONS_SCHEMA, empty_columns, to_table
from ...taxonomy import classify_organization
from . import entities as wd

COMPANY_WORDS = ("company", "business", "enterprise", "bank", "airline", "conglomerate", "corporation", "firm",
                 "manufacturer", "retail chain", "restaurant chain", "operator", "publisher", "subsidiary")


def entity_type(type_labels: list[str]) -> str:
    text = " ".join(type_labels).lower()
    if "brand" in text:
        return "brand"
    if any(word in text for word in COMPANY_WORDS):
        return "company"
    return "organization"


def labels_of(files: list[str]) -> dict[str, str]:
    labels: dict[str, str] = {}
    for path in files:
        for batch in pq.ParquetFile(path).iter_batches(batch_size=20_000, columns=["qid", "label_en", "label_ur"]):
            data = batch.to_pydict()
            for q, en, ur in zip(data["qid"], data["label_en"], data["label_ur"]):
                if en or ur:
                    labels[q] = en or ur
    return labels


def organization_row(entity: dict, record: dict, *, labels: dict[str, str], country: str, country_qid: str | None,
                     context: dict) -> dict:
    name = wd.any_label(entity)
    names = lambda prop: [labels[q] for q in wd.item_ids(entity, prop) if q in labels]  # noqa: E731
    type_labels = [labels.get(c) for c in (record["classes"] or wd.item_ids(entity, "P31"))]
    type_labels = [t for t in type_labels if t]
    industries = names("P452")
    url, domain, tld, is_platform = normalize_website(wd.first_string(entity, "P856"))
    ids = wd.identifiers(entity)
    alias_list = wd.aliases(entity)
    legal_name = wd.monolingual(entity, "P1448")
    lat, lon = wd.coordinates(entity)
    phone = wd.first_string(entity, "P1329")
    email = wd.first_string(entity, "P968")
    parent = wd.item_ids(entity, "P749") or wd.item_ids(entity, "P127")
    countries = wd.item_ids(entity, "P17")
    headquarters = names("P159") or names("P131")
    keys = sorted({k for k in (name_key(a) for a in alias_list + ([legal_name] if legal_name else [])) if k}
                  - {name_key(name)})
    qid = record["qid"]
    return {
        "silver_id": f"wikidata:{qid}", "source": "wikidata", "source_record_id": qid,
        "name": name, "name_local": wd.label(entity, "ur"), "legal_name": legal_name,
        "normalized_name": normalize_name(name), "name_key": name_key(name),
        "aliases": alias_list or None, "alias_keys": keys or None,
        "description": entity.get("descriptions", {}).get("en", {}).get("value"),
        "entity_type": entity_type(type_labels), "org_types": type_labels or None, "industries": industries or None,
        "category_group": classify_organization(type_labels + industries),
        "country_code": country if country_qid in countries else None,
        "jurisdiction": None,
        "headquarters": headquarters[0] if headquarters else None,
        "latitude": lat, "longitude": lon, "address": None,
        "website": url, "normalized_domain": domain, "tld": tld,
        "domain_is_platform": is_platform if domain else None,
        "email": normalize_email(email.removeprefix("mailto:") if email else None),
        "phone": phone, "normalized_phone": normalize_phone(phone, country),
        "social_links": wd.social_links(ids),
        "inception": wd.date(entity, "P571"), "dissolved": wd.date(entity, "P576"),
        "status": "dissolved" if wd.date(entity, "P576") else None,
        "legal_form": (names("P1454") or [None])[0], "registry_id": None,
        "parent_id": f"wikidata:{parent[0]}" if parent else None,
        "parent_name": labels.get(parent[0]) if parent else None,
        "subsidiaries": names("P355") or None, "founders": names("P112") or None, "brands": names("P1716") or None,
        "employees": wd.quantity(entity, "P1128"),
        "wikidata": qid, "identifiers": json.dumps(ids, sort_keys=True),
        "source_url": f"https://www.wikidata.org/wiki/{qid}",
        "source_confidence": None,
        "source_updated_at": wd.modified(entity), "source_version": context["source_version"],
        "artifact_id": context["artifact_id"], "bronze_version_id": context["bronze_version_id"],
        "pipeline_run_id": context["run_id"], "transformation_version": context["transformation_version"],
        "ingested_at": context["now"],
        "attributes": json.dumps({"role": record["role"], "revision": record["revision"],
                                  "classes": record["classes"], "countries": [labels.get(c, c) for c in countries],
                                  "sitelinks": {k: v.get("url") for k, v in entity.get("sitelinks", {}).items()}},
                                 ensure_ascii=False),
    }


def transform(ctx: StageContext, artifact: dict, bronze: dict, files: list[str], transformation_version: str):
    country = artifact["country"]
    country_qid = artifact["metadata"].get("country_qid")
    partition = {"source": "wikidata", "country": country}
    labels = labels_of(files)
    context = {"source_version": artifact["source_version"], "artifact_id": artifact["id"],
               "bronze_version_id": bronze["id"], "run_id": ctx.run_id,
               "transformation_version": transformation_version,
               "now": datetime.now(timezone.utc).replace(microsecond=0)}
    stats = {"items": 0, "organizations": 0, "referenced": 0, "unnamed": 0}
    inputs = [{"type": "artifact", "id": artifact["id"]},
              {"type": "dataset_version", "dataset_id": "bronze.wikidata_entities", "id": bronze["id"]}]
    with DatasetVersionWriter(ctx.storage, "silver.organizations", partition, ORGANIZATIONS_SCHEMA,
                              run_id=ctx.run_id, versions={"transformation": transformation_version},
                              inputs=inputs) as writer:
        for path in files:
            for batch in pq.ParquetFile(path).iter_batches(batch_size=2000,
                                                           columns=["qid", "role", "revision", "classes",
                                                                    "entity_json"]):
                columns = empty_columns(ORGANIZATIONS_SCHEMA)
                for record in batch.to_pylist():
                    if record["role"] == "label":
                        continue
                    stats["items"] += 1
                    entity = json.loads(record["entity_json"])
                    row = organization_row(entity, record, labels=labels, country=country, country_qid=country_qid,
                                           context=context)
                    if not row["name"]:
                        stats["unnamed"] += 1
                        continue
                    stats[record["role"] if record["role"] in stats else "organizations"] += 1
                    for key in columns:
                        columns[key].append(row.get(key))
                writer.write_batch(to_table(columns, ORGANIZATIONS_SCHEMA))
                ctx.progress(stats["items"], None, "entities", "extracting organizations")
    stats["organizations"] = stats.pop("organization", 0) or stats["organizations"]
    return writer.version, stats
