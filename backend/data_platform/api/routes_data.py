"""Data-plane APIs: dataset catalog, Data Explorer queries, provenance, match review, saved views."""

from __future__ import annotations

import json
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .. import db, repository
from ..errors import ParameterError
from ..lake.reader import dataset_files, duck, parquet_source
from ..query import service
from ..query.dsl import Group, QueryRequest
from ..query.registry import DATASETS, DatasetSpec, get_dataset
from ..query.service import jsonable
from .auth import EXPORT_DATA, REVIEW_MATCHES, VIEW, Principal, current_principal, require

router = APIRouter(prefix="/api/data-platform", tags=["data-platform-data"])
Viewer = Annotated[Principal, Depends(require(VIEW))]


def dataset_for(dataset_id: str, principal: Principal) -> DatasetSpec:
    try:
        spec = get_dataset(dataset_id)
    except KeyError as error:
        raise HTTPException(404, str(error)) from None
    if not principal.can(spec.permission):
        raise HTTPException(403, f"Missing permission {spec.permission}")
    return spec


def handle_parameter_errors(fn):
    try:
        return fn()
    except ParameterError as error:
        raise HTTPException(422, str(error)) from None


@router.get("/datasets")
def list_datasets(principal: Viewer):
    catalog = {d["id"]: d for d in repository.list_datasets()}
    result = []
    for spec in DATASETS:
        entry = catalog.get(spec.id, {})
        result.append(jsonable({
            "id": spec.id, "layer": spec.layer, "label": spec.label, "description": spec.description,
            "engine": spec.engine, "accessible": principal.can(spec.permission), "permission": spec.permission,
            "row_count": entry.get("row_count"), "size_bytes": entry.get("size_bytes"),
            "partitions": entry.get("partitions"), "last_committed_at": entry.get("last_committed_at"),
            "partition_columns": entry.get("partition_columns"),
        }))
    return result


@router.get("/datasets/{dataset_id}/schema")
def dataset_schema(dataset_id: str, principal: Annotated[Principal, Depends(current_principal)]):
    spec = dataset_for(dataset_id, principal)
    versions = repository.current_versions(spec.id) if spec.engine == "parquet" else []
    physical = {}
    for version in versions:
        for field in (version.get("schema") or {}).get("fields", []):
            physical.setdefault(field["name"], field["type"])
    registered = {c.name for c in spec.columns}
    return jsonable({
        "dataset": spec.id, "primary_key": spec.primary_key,
        "columns": [{**c.describe(), "physical_type": physical.get(c.name)} for c in spec.columns],
        # Columns present in the data but not yet described: schema evolution is visible, not hidden.
        "unregistered_columns": [{"name": n, "type": t} for n, t in physical.items() if n not in registered],
        "versions": [{"id": v["id"], "partition": v["partition"], "rows": v["row_count"], "schema_hash": v["schema_hash"],
                      "versions": v["versions"], "committed_at": v["committed_at"]} for v in versions],
    })


@router.get("/datasets/{dataset_id}/filters")
def dataset_filters(dataset_id: str, principal: Annotated[Principal, Depends(current_principal)]):
    spec = dataset_for(dataset_id, principal)
    return [c.describe() for c in spec.columns if c.filterable]


@router.post("/datasets/{dataset_id}/query")
def query_dataset(dataset_id: str, body: QueryRequest, principal: Annotated[Principal, Depends(current_principal)]):
    spec = dataset_for(dataset_id, principal)
    return handle_parameter_errors(lambda: service.run_query(spec, body))


class ValuesRequest(BaseModel):
    filters: Group | None = None
    search: str | None = None
    limit: int = Field(default=50, ge=1, le=500)


@router.post("/datasets/{dataset_id}/values/{field}")
def field_values(dataset_id: str, field: str, body: ValuesRequest,
                 principal: Annotated[Principal, Depends(current_principal)]):
    spec = dataset_for(dataset_id, principal)
    return handle_parameter_errors(lambda: service.facet_values(spec, field, search=body.search,
                                                                filters=body.filters, limit=body.limit))


@router.post("/datasets/{dataset_id}/export")
def export_dataset(dataset_id: str, body: QueryRequest,
                   principal: Annotated[Principal, Depends(require(EXPORT_DATA))]):
    spec = dataset_for(dataset_id, principal)
    handle_parameter_errors(lambda: service.run_query(spec, body.model_copy(update={"page": 1, "page_size": 1})))
    return StreamingResponse(service.export_csv(spec, body), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{spec.id}.csv"'})


@router.get("/datasets/{dataset_id}/records/{key}")
def get_record(dataset_id: str, key: str, principal: Annotated[Principal, Depends(current_principal)]):
    spec = dataset_for(dataset_id, principal)
    record = handle_parameter_errors(lambda: service.get_record(spec, key))
    if record is None:
        raise HTTPException(404, "Record not found")
    result = {"record": record}
    if spec.id == "gold.businesses":
        result["provenance"] = business_provenance(key)
        if record.get("organization_id"):
            result["organization"] = organization_provenance(record["organization_id"], record.get("country_code"))
    elif spec.id == "gold.organizations":
        result["organization"] = organization_provenance(key, record.get("country_code"))
    return result


def business_provenance(gold_id: str) -> dict:
    """Every source record behind a Gold business, with its artifact, run, versions and match evidence."""
    with db.serving() as conn:
        members = conn.execute("SELECT * FROM gold.business_sources WHERE gold_id = %s ORDER BY is_primary DESC, silver_id",
                               [gold_id]).fetchall()
        ids = [m["silver_id"] for m in members]
        matches = conn.execute("""
            SELECT * FROM er.entity_matches WHERE (left_id = ANY(%s) OR right_id = ANY(%s)) AND match_type = 'location'
            ORDER BY match_score DESC
        """, [ids, ids]).fetchall() if ids else []
    silver_records = {}
    files = dataset_files("silver.places")
    if ids and files:
        con = duck()
        try:
            result = con.execute(f"SELECT * EXCLUDE (attributes), attributes FROM {parquet_source(files)} "
                                 f"WHERE silver_id IN ({', '.join(['?'] * len(ids))})", ids)
            names = [d[0] for d in result.description]
            for row in result.fetchall():
                record = dict(zip(names, row))
                record["attributes"] = json.loads(record["attributes"]) if record["attributes"] else {}
                silver_records[record["silver_id"]] = record
        finally:
            con.close()
    artifacts = {a: repository.get_artifact(a) for a in {m["artifact_id"] for m in members if m["artifact_id"]}}
    return jsonable({
        "sources": [{**m, "silver": silver_records.get(m["silver_id"]),
                     "artifact": artifacts.get(m["artifact_id"])} for m in members],
        "matches": matches,
    })


def organization_provenance(organization_id: str, country: str | None) -> dict:
    """An organization's linked records (Wikidata, registries) with how each was linked, plus its locations."""
    with db.serving() as conn:
        organization = conn.execute("""
            SELECT * FROM gold.organizations WHERE organization_id = %s AND (%s::text IS NULL OR country_code = %s)
        """, [organization_id, country, country]).fetchone()
        members = conn.execute("""
            SELECT * FROM gold.organization_sources WHERE organization_id = %s AND (%s::text IS NULL OR country_code = %s)
            ORDER BY source, silver_id
        """, [organization_id, country, country]).fetchall()
        locations = conn.execute("""
            SELECT gold_id, name, city, sources FROM gold.businesses
            WHERE organization_id = %s AND (%s::text IS NULL OR country_code = %s)
            ORDER BY city NULLS LAST, name LIMIT 25
        """, [organization_id, country, country]).fetchall()
    records = {}
    files = dataset_files("silver.organizations")
    ids = [m["silver_id"] for m in members]
    if ids and files:
        con = duck()
        try:
            result = con.execute(f"SELECT * EXCLUDE (attributes), attributes FROM {parquet_source(files)} "
                                 f"WHERE silver_id IN ({', '.join(['?'] * len(ids))})", ids)
            names = [d[0] for d in result.description]
            for row in result.fetchall():
                record = dict(zip(names, row))
                record["attributes"] = json.loads(record["attributes"]) if record["attributes"] else {}
                record["identifiers"] = json.loads(record["identifiers"]) if record.get("identifiers") else {}
                records[record["silver_id"]] = record
        finally:
            con.close()
    return jsonable({"organization": organization,
                     "records": [{**m, "silver": records.get(m["silver_id"])} for m in members],
                     "locations": locations})


class MatchDecision(BaseModel):
    status: Literal["accepted", "rejected", "suggested"]


@router.post("/matches/{match_id}/decision")
def decide_match(match_id: int, body: MatchDecision, principal: Annotated[Principal, Depends(require(REVIEW_MATCHES))]):
    """Human review. Takes effect in Gold on the next GOLD_BUILD (the ER fingerprint changes)."""
    with db.serving() as conn:
        row = conn.execute("""
            UPDATE er.entity_matches SET status = %s, decided_by = %s, decided_at = now(), updated_at = now()
            WHERE id = %s RETURNING *
        """, [body.status, principal.name, match_id]).fetchone()
    if row is None:
        raise HTTPException(404, "Match not found")
    return jsonable(row)


# ---------------------------------------------------------------- saved views

class SavedView(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    dataset_id: str
    query: QueryRequest
    shared: bool = False


@router.get("/views")
def list_views(principal: Viewer, dataset_id: str | None = None):
    with db.control() as conn:
        rows = conn.execute("""
            SELECT * FROM dp.saved_views WHERE (owner = %(owner)s OR shared)
              AND (%(dataset)s::text IS NULL OR dataset_id = %(dataset)s) ORDER BY lower(name)
        """, {"owner": principal.name, "dataset": dataset_id}).fetchall()
    return jsonable(rows)


@router.post("/views", status_code=201)
def create_view(body: SavedView, principal: Viewer):
    spec = dataset_for(body.dataset_id, principal)
    handle_parameter_errors(lambda: service.run_query(spec, body.query.model_copy(update={"page_size": 1})))
    with db.control() as conn:
        row = conn.execute("""
            INSERT INTO dp.saved_views (name, description, dataset_id, query, owner, shared)
            VALUES (%s, %s, %s, %s, %s, %s) RETURNING *
        """, [body.name, body.description, body.dataset_id, db.Jsonb(body.query.model_dump(mode="json")),
              principal.name, body.shared]).fetchone()
    return jsonable(row)


@router.delete("/views/{view_id}")
def delete_view(view_id: int, principal: Viewer):
    with db.control() as conn:
        deleted = conn.execute("DELETE FROM dp.saved_views WHERE id = %s AND owner = %s", [view_id, principal.name]).rowcount
    if not deleted:
        raise HTTPException(404, "View not found (or not yours)")
    return {"ok": True}
