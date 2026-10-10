"""Adapter registry.

Built-in adapters are listed in BUILTIN_ADAPTERS; extra plugins can be added without
code changes via DATA_PLATFORM_EXTRA_SOURCES="package.module:AdapterClass,...".
The engine only ever talks to adapters through this registry.
"""

import importlib
import os
from functools import lru_cache

from .. import repository
from .base import DataSourceAdapter, PipelineSpec

BUILTIN_ADAPTERS = [
    "data_platform.sources.osm.adapter:OSMAdapter",
    "data_platform.sources.overture.adapter:OvertureAdapter",
    "data_platform.sources.geonames.adapter:GeoNamesAdapter",
    "data_platform.sources.wikidata.adapter:WikidataAdapter",
    "data_platform.sources.commoncrawl.adapter:CommonCrawlAdapter",
    "data_platform.sources.gdelt.adapter:GDELTAdapter",
    "data_platform.sources.opencorporates.adapter:OpenCorporatesAdapter",
]


def _load(path: str) -> DataSourceAdapter:
    module_name, _, class_name = path.partition(":")
    adapter_class = getattr(importlib.import_module(module_name), class_name)
    if not issubclass(adapter_class, DataSourceAdapter):
        raise TypeError(f"{path} is not a DataSourceAdapter")
    return adapter_class()


@lru_cache
def adapters() -> dict[str, DataSourceAdapter]:
    paths = list(BUILTIN_ADAPTERS)
    extra = os.environ.get("DATA_PLATFORM_EXTRA_SOURCES", "")
    paths += [p.strip() for p in extra.split(",") if p.strip()]
    loaded = {}
    for path in paths:
        adapter = _load(path)
        if adapter.source_id in loaded:
            raise ValueError(f"Duplicate source id {adapter.source_id!r}")
        loaded[adapter.source_id] = adapter
    return loaded


def register(adapter: DataSourceAdapter) -> None:
    """Add an adapter at runtime (used by tests and embedding applications)."""
    adapters()[adapter.source_id] = adapter


def get_adapter(source_id: str) -> DataSourceAdapter:
    try:
        return adapters()[source_id]
    except KeyError:
        raise KeyError(f"No adapter registered for source {source_id!r}") from None


def find_pipeline(definition_id: str) -> tuple[DataSourceAdapter, PipelineSpec]:
    for adapter in adapters().values():
        for pipeline in adapter.pipelines():
            if pipeline.id == definition_id:
                return adapter, pipeline
    raise KeyError(f"Unknown pipeline {definition_id!r}")


def sync_to_control_plane() -> None:
    """Upsert sources, pipeline definitions and datasets declared by adapters."""
    from ..lake.catalog import register_platform_datasets

    for adapter in adapters().values():
        repository.upsert_source(adapter.source_id, adapter.name, adapter.description, adapter.homepage,
                                 adapter.license, adapter.capabilities.as_dict())
        for pipeline in adapter.pipelines():
            repository.upsert_pipeline_definition(
                pipeline.id, adapter.source_id, pipeline.name, pipeline.description,
                pipeline.parameter_schema(), pipeline.defaults, [str(s) for s in pipeline.stages],
            )
        for dataset in adapter.datasets():
            repository.register_dataset(**dataset)
    register_platform_datasets()


def counted_artifacts_sql() -> str:
    """SQL (a SELECT of id, source_id) for the artifacts whose Bronze counts as the current raw data.

    Snapshot sources (a new extract replaces the old one): the latest artifact per source and country.
    Accumulating sources (each artifact is a separate news window or lookup batch): all of them.
    """
    accumulating = sorted(a.source_id for a in adapters().values() if a.bronze_accumulates)
    listed = ", ".join("'" + source.replace("'", "''") + "'" for source in accumulating) or "NULL"
    with_bronze = """status = 'available' AND EXISTS (
        SELECT 1 FROM dp.dataset_versions v WHERE v.is_current AND (v.partition->>'artifact_id')::bigint = a.id)"""
    return f"""
        SELECT * FROM (
            SELECT DISTINCT ON (source_id, country) id, source_id FROM dp.dataset_artifacts a
            WHERE {with_bronze} AND source_id NOT IN ({listed})
            ORDER BY source_id, country, created_at DESC
        ) latest_snapshots
        UNION ALL
        SELECT id, source_id FROM dp.dataset_artifacts a WHERE {with_bronze} AND source_id IN ({listed})
    """
