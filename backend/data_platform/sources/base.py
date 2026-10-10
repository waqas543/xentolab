"""The source plugin contract.

A source adapter knows how to discover, download, validate and parse one provider
into Bronze, and how to normalize its Bronze into the shared Silver contract.
Entity resolution, Gold and serving are platform stages that work on Silver, so a
new adapter never requires changes to the orchestration engine or to Gold.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import jsonschema

from ..errors import ParameterError
from ..states import Stage

if TYPE_CHECKING:
    from ..engine.context import StageContext
    from ..quality.checks import QualityCheck

# Parameters every pipeline accepts. Adapters add their own on top.
COMMON_PARAMETERS: dict[str, dict] = {
    "force_download": {"type": "boolean", "default": False, "title": "Force download",
                       "description": "Download again even if an identical artifact exists."},
    "rebuild_bronze": {"type": "boolean", "default": False, "title": "Rebuild Bronze",
                       "description": "Re-parse the artifact even if Bronze already exists for it."},
    "rebuild_silver": {"type": "boolean", "default": False, "title": "Rebuild Silver"},
    "rebuild_gold": {"type": "boolean", "default": False, "title": "Rebuild Gold",
                     "description": "Re-run entity resolution and Gold even if inputs are unchanged."},
    "start_stage": {"type": "string", "default": "DISCOVER", "title": "Start at stage (backfill)",
                    "enum": [str(Stage.DISCOVER), str(Stage.BRONZE_INGEST), str(Stage.SILVER_TRANSFORM),
                             str(Stage.ENTITY_RESOLUTION), str(Stage.GOLD_BUILD)],
                    "description": "Skip earlier stages and rebuild from existing data."},
    "artifact_id": {"type": ["integer", "null"], "default": None, "title": "Artifact ID (backfill)",
                    "description": "Rebuild from this stored artifact instead of discovering a new one."},
    "er_algorithm": {"type": "string", "default": "v1", "title": "Entity resolution algorithm",
                     "enum": ["v1"]},
}

SECRET_PARAMETER_NAMES = ("password", "secret", "token", "api_key", "apikey", "credential")


@dataclass(frozen=True)
class SourceCapabilities:
    supports_download: bool = True
    supports_incremental: bool = False       # e.g. OSM replication diffs (future)
    supports_release_detection: bool = False  # cheap "is there a new version?" check
    provides_places: bool = False             # contributes to silver.places / Gold businesses
    provides_organizations: bool = False
    provides_geography: bool = False
    credentials: tuple[str, ...] = ()         # secret names the adapter needs (DP_SECRET_<NAME>), never values

    def as_dict(self) -> dict:
        values = dict(self.__dict__)
        values["credentials"] = list(self.credentials)
        return values


@dataclass(frozen=True)
class PipelineSpec:
    id: str                                   # e.g. "osm.extract"
    name: str
    description: str
    parameters: dict[str, dict]               # JSON-schema properties (adapter-specific)
    required: list[str] = field(default_factory=list)
    defaults: dict[str, Any] = field(default_factory=dict)
    stages: list[Stage] = field(default_factory=lambda: list(Stage))

    def parameter_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {**self.parameters, **COMMON_PARAMETERS},
            "required": self.required,
            "additionalProperties": False,
        }


@dataclass(frozen=True)
class DiscoveredDataset:
    """One downloadable dataset the provider offers (an extract, a release, a dump)."""

    dataset_name: str
    url: str | None
    source_version: str | None = None
    release_date: str | None = None
    size_bytes: int | None = None
    checksum: str | None = None               # provider-published checksum, e.g. "md5:..."
    country: str | None = None
    region: str | None = None
    format: str | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class StageResult:
    records_in: int | None = None
    records_out: int | None = None
    records_rejected: int | None = None
    bytes_processed: int | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    skipped_reason: str | None = None         # set when the stage was an idempotent no-op

    @classmethod
    def skipped(cls, reason: str, **kwargs) -> "StageResult":
        return cls(skipped_reason=reason, **kwargs)


@dataclass(frozen=True)
class StagePolicy:
    max_attempts: int = 1
    base_delay_seconds: float = 5.0
    max_delay_seconds: float = 300.0
    required: bool = True                     # failure of an optional stage => PARTIALLY_COMPLETED


DEFAULT_POLICIES: dict[Stage, StagePolicy] = {
    Stage.DISCOVER: StagePolicy(max_attempts=4, base_delay_seconds=5, max_delay_seconds=120),
    Stage.DOWNLOAD: StagePolicy(max_attempts=5, base_delay_seconds=10, max_delay_seconds=300),
    Stage.ANALYTICS_REFRESH: StagePolicy(max_attempts=2, base_delay_seconds=5, required=False),
}


class DataSourceAdapter(ABC):
    source_id: str
    name: str
    description: str = ""
    homepage: str | None = None
    license: str | None = None
    capabilities: SourceCapabilities = SourceCapabilities()
    parser_version: str = "1"
    transformation_version: str = "1"
    role: str = ""                            # what it contributes, shown in the UI ("Locations", "News signals"…)
    # True when each artifact is a distinct slice (a news window, a batch of lookups) rather than a full
    # snapshot: raw-record counts then add up all artifacts instead of taking the latest one.
    bronze_accumulates: bool = False

    # -- declaration ---------------------------------------------------------------

    @abstractmethod
    def pipelines(self) -> list[PipelineSpec]: ...

    def concurrency_key(self, params: dict) -> str:
        """Runs with the same key never execute at the same time (they write the same partitions)."""
        return f"{self.source_id}:{params.get('country') or params.get('region') or 'global'}"

    def run_labels(self, params: dict) -> tuple[str | None, str | None]:
        """(country, dataset) shown on the run, used for filtering."""
        return params.get("country"), params.get("region")

    def stage_policy(self, stage: Stage) -> StagePolicy:
        return DEFAULT_POLICIES.get(stage, StagePolicy())

    def validate_parameters(self, pipeline: PipelineSpec, params: dict) -> dict:
        merged = {key: spec.get("default") for key, spec in pipeline.parameter_schema()["properties"].items()
                  if "default" in spec}
        merged.update(pipeline.defaults)
        merged.update({k: v for k, v in (params or {}).items()})
        lowered = {k.lower() for k in merged}
        if any(secret in name for name in lowered for secret in SECRET_PARAMETER_NAMES):
            raise ParameterError("Credentials must come from the environment, not pipeline parameters")
        try:
            jsonschema.validate(merged, pipeline.parameter_schema())
        except jsonschema.ValidationError as error:
            path = ".".join(str(p) for p in error.absolute_path) or "parameters"
            raise ParameterError(f"{path}: {error.message}") from None
        return self.check_parameters(merged)

    def check_parameters(self, params: dict) -> dict:
        """Hook for cross-field validation / normalization. Return the final params."""
        return params

    def latest_release(self, params: dict) -> DiscoveredDataset | None:
        """Cheap check for the newest provider release (for on_release schedules). None = unsupported."""
        return None

    def latest_artifact(self, params: dict) -> dict | None:
        """The newest stored artifact for these parameters (used by backfills without artifact_id)."""
        from .. import db

        country, _ = self.run_labels(params)
        with db.control() as conn:
            return conn.execute("""
                SELECT * FROM dp.dataset_artifacts
                WHERE source_id = %s AND status = 'available' AND (%s::text IS NULL OR country = %s)
                ORDER BY coalesce(source_release_date, created_at) DESC, id DESC LIMIT 1
            """, [self.source_id, country, country]).fetchone()

    # -- stage implementations -----------------------------------------------------

    @abstractmethod
    def discover_available_datasets(self, ctx: StageContext) -> list[DiscoveredDataset]: ...

    @abstractmethod
    def download(self, ctx: StageContext) -> StageResult:
        """Store the artifact (never overwriting) and set ctx.state['artifact_id']."""

    @abstractmethod
    def validate_download(self, artifact: dict, ctx: StageContext) -> StageResult: ...

    @abstractmethod
    def ingest_bronze(self, artifact: dict, ctx: StageContext) -> StageResult: ...

    @abstractmethod
    def transform_silver(self, ctx: StageContext) -> StageResult: ...

    def build_gold(self, ctx: StageContext) -> StageResult | None:
        """Optional source-specific Gold extras. Shared Gold is built by the platform."""
        return None

    def quality_checks(self, layer: str, dataset_id: str) -> list[QualityCheck]:
        """Default data-quality suite for a dataset this adapter writes."""
        return []

    def datasets(self) -> list[dict]:
        """Bronze/Silver datasets this adapter writes, for the catalog."""
        return []

    @abstractmethod
    def collect_metrics(self, ctx: StageContext) -> dict[str, float]: ...
