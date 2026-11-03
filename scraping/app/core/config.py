"""Job configuration schema (config/jobs/*.yaml) and extraction rules (config/extraction_rules.yaml)."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.core.exceptions import ConfigError
from config.settings import EXTRACTION_RULES_FILE, JOBS_DIR, PROJECT_ROOT, Settings, get_settings

__all__ = ["ExtractionRules", "JobConfig", "Settings", "get_settings", "list_jobs", "load_job_config"]

FIXED_PREFIX_RECORD_TYPES = ("errors", "skipped")
DEFAULT_EXCLUDE_PATTERNS = [
    r"\.(?:jpe?g|png|gif|webp|svg|ico|pdf|zip|rar|docx?|xlsx?|pptx?|mp4|mp3|avi|css|js|woff2?)(?:\?|$)",
    r"^mailto:", r"^tel:", r"^javascript:", r"/wp-json/", r"/feed/?$",
]


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _compile_all(patterns: list[str]) -> list[str]:
    for pattern in patterns:
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"invalid regex {pattern!r}: {exc}") from exc
    return patterns


class RateLimitConfig(_Section):
    requests_per_second: float = Field(default=1.0, gt=0)
    max_concurrency: int = Field(default=3, ge=1, le=100)


class RetryConfig(_Section):
    max_attempts: int = Field(default=3, ge=1, le=20)
    backoff_seconds: float = Field(default=2.0, ge=0)
    max_backoff_seconds: float = Field(default=60.0, ge=0)
    retry_on_status: list[int] = Field(default_factory=lambda: [408, 425, 429, 500, 502, 503, 504])


class NameNormalizationConfig(_Section):
    strip_suffixes: bool = True
    extra_suffixes: list[str] = Field(default_factory=list)
    min_words_after_strip: int = Field(default=2, ge=1,
                                       description="Keep the suffix if stripping leaves fewer words (Systems Limited)")
    remove_chars: str = Field(default=r"[^\w\s&.,'\-()/+]", description="Regex of characters to drop")


LoaderName = Literal["load_page", "scrape_api", "http", "playwright"]


class LoaderOptions(_Section):
    """Browser options for the scrape_api loader (ignored by the others)."""

    wait_until: Literal["none", "domcontentloaded", "load", "networkidle"] = "networkidle"
    wait_for_selector: str | None = Field(default=None, description="Also wait until this CSS selector is attached")
    wait_for_timeout_ms: int | None = Field(default=None, ge=100)
    block_resources: list[Literal["image", "media", "font", "stylesheet"]] = Field(default_factory=list)


class SearchEngineConfig(_Section):
    """One search engine. Fetch settings left unset fall back to the job's (loader, render_js, rate_limit, retry)."""

    name: Literal["duckduckgo", "google"]
    url_template: str | None = Field(default=None, description="Fields: query, region, language, offset")
    region: str | None = Field(default=None, description="DuckDuckGo kl (pk-en) / Google gl (pk, us)")
    language: str = "en"
    max_pages: int = Field(default=1, ge=1, le=5)
    page_size: int = Field(default=10, ge=1)
    loader: LoaderName | None = None
    loader_options: LoaderOptions | None = None
    render_js: bool | None = None
    rate_limit: RateLimitConfig | None = None
    retry: RetryConfig | None = None
    cooldown_seconds: float = Field(default=300.0, ge=0,
                                    description="Skip the engine this long after it blocks us (doubles while blocked)")
    resolve_links: bool = Field(default=True, description="Google: resolve /goto redirect links to the real URLs")


class SearchConfig(_Section):
    query_template: str = "{name} {city} Pakistan"
    mode: Literal["fallback", "all"] = Field(
        default="fallback", description="fallback: first engine that answers; all: every engine, results merged")
    engines: list[SearchEngineConfig] = Field(default_factory=lambda: [SearchEngineConfig(name="duckduckgo")],
                                              min_length=1)

    @field_validator("engines")
    @classmethod
    def _unique_engines(cls, engines: list[SearchEngineConfig]) -> list[SearchEngineConfig]:
        names = [engine.name for engine in engines]
        if len(names) != len(set(names)):
            raise ValueError(f"search engines listed twice: {names}")
        return engines


class WebsiteConfig(_Section):
    """Optional crawl of the likely company domain found through search."""

    enabled: bool = True
    max_pages: int = Field(default=3, ge=1, le=50)
    max_depth: int = Field(default=1, ge=0, le=5)
    include_patterns: list[str] = Field(
        default_factory=lambda: [r"about", r"contact", r"company", r"profile", r"who-we-are", r"reach-us"])
    exclude_patterns: list[str] = Field(default_factory=lambda: list(DEFAULT_EXCLUDE_PATTERNS))

    @field_validator("include_patterns", "exclude_patterns")
    @classmethod
    def _valid_regexes(cls, patterns: list[str]) -> list[str]:
        return _compile_all(patterns)


class SummaryConfig(_Section):
    """Company profile + summary from the crawled website, by a local model on Ollama (OLLAMA_URL)."""

    enabled: bool = False
    model: str = "qwen3:8b"
    text_budget: int = Field(default=12_000, ge=1000, description="Characters of page text sent to the model")
    num_ctx: int = Field(default=8192, ge=2048)
    timeout_seconds: float = Field(default=300.0, gt=0)
    max_concurrency: int = Field(default=1, ge=1, le=8)


class LinkedInConfig(_Section):
    """The company's public LinkedIn page (only the main page: everything else is behind the login wall).
    Fetch settings left unset fall back to the job's."""

    enabled: bool = False
    loader: LoaderName | None = None
    loader_options: LoaderOptions | None = None
    render_js: bool | None = False
    rate_limit: RateLimitConfig | None = None
    retry: RetryConfig | None = Field(default=None, description="The login wall comes and goes: retry it")
    max_posts: int = Field(default=5, ge=0, le=20, description="Recent posts kept from the page")


class CrawlConfig(_Section):
    max_depth: int = Field(default=1, ge=0, le=10)
    max_pages: int = Field(default=50, ge=1)
    allowed_domains: list[str] = Field(default_factory=list, description="Empty = the seed URLs' domains")
    include_patterns: list[str] = Field(default_factory=list, description="Empty = follow every allowed URL")
    exclude_patterns: list[str] = Field(default_factory=lambda: list(DEFAULT_EXCLUDE_PATTERNS))

    @field_validator("include_patterns", "exclude_patterns")
    @classmethod
    def _valid_regexes(cls, patterns: list[str]) -> list[str]:
        return _compile_all(patterns)


class ExtractionOverrides(_Section):
    region: str | None = None
    extra_cities: dict[str, str] = Field(default_factory=dict, description="city -> province")
    extra_address_keywords: list[str] = Field(default_factory=list)
    extra_generic_domains: dict[str, list[str]] = Field(default_factory=dict, description="category -> domains")
    min_likely_domain_score: float = 45.0


class JobConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_name: str = Field(pattern=r"^[a-z0-9_]+$")
    job_type: str | None = Field(default=None, description="Registered job class; defaults to job_name")
    description: str = ""
    source: str = "fbr"

    # input
    input_file: Path | None = None
    input_format: Literal["auto", "jsonl", "csv"] = "auto"
    company_name_field: str | list[str] = "company_name"
    fallback_company_name_field: str | list[str] = "business_name"
    field_map: dict[str, list[str]] = Field(default_factory=lambda: {
        "ntn": ["ntn", "NTN", "ntn_no"],
        "registration_no": ["registration_no", "registration_number", "reg_no", "incorporation_no"],
        "province": ["province", "PROVINCE", "state"],
        "city": ["city", "CITY", "district"],
        "address": ["address", "ADDRESS", "business_address"],
    })
    name_normalization: NameNormalizationConfig = Field(default_factory=NameNormalizationConfig)

    # search + website enrichment
    search: SearchConfig = Field(default_factory=SearchConfig)
    max_results_per_company: int = Field(default=20, ge=1, le=100)
    website: WebsiteConfig = Field(default_factory=WebsiteConfig)
    summary: SummaryConfig = Field(default_factory=SummaryConfig)
    linkedin: LinkedInConfig = Field(default_factory=LinkedInConfig)

    # generic crawling
    seed_urls: list[str] = Field(default_factory=list)
    crawl: CrawlConfig = Field(default_factory=CrawlConfig)

    # fetching
    loader: LoaderName = "load_page"
    loader_options: LoaderOptions = Field(default_factory=LoaderOptions)
    render_js: bool = True
    timeout_seconds: float = Field(default=90.0, gt=0)
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)
    retry: RetryConfig = Field(default_factory=RetryConfig)

    # processing + output
    parsers: list[str] = Field(default_factory=list)
    writers: list[str] = Field(default_factory=lambda: ["jsonl_writer"])
    output_dir: Path = Path("output/data")
    output_prefix: str | None = Field(default=None, description="Defaults to job_name")
    outputs: dict[str, str] = Field(default_factory=dict, description="record_type -> file prefix override")
    output_subdirs: dict[str, str] = Field(default_factory=lambda: {"linkedin_companies": "linkedin"},
                                           description="record_type -> folder inside output_dir")
    max_records_per_jsonl_file: int = Field(default=1000, ge=1)
    state_dir: Path = Path("output/state")
    extraction: ExtractionOverrides = Field(default_factory=ExtractionOverrides)
    options: dict[str, Any] = Field(default_factory=dict, description="Free-form settings for custom jobs")

    @model_validator(mode="after")
    def _resolve_paths(self) -> JobConfig:
        for name in ("input_file", "output_dir", "state_dir"):
            value = getattr(self, name)
            if value is not None and not value.is_absolute():
                object.__setattr__(self, name, PROJECT_ROOT / value)
        return self

    @property
    def type(self) -> str:
        return self.job_type or self.job_name

    @property
    def company_name_fields(self) -> list[str]:
        return _as_list(self.company_name_field)

    @property
    def fallback_company_name_fields(self) -> list[str]:
        return _as_list(self.fallback_company_name_field)

    @property
    def state_path(self) -> Path:
        return self.state_dir / f"{self.job_name}.sqlite"

    def with_overrides(self, section: SearchEngineConfig | LinkedInConfig) -> JobConfig:
        """This config with a section's own fetch settings (a search engine, LinkedIn) applied; itself when the
        section overrides nothing."""
        updates = {name: value for name in ("loader", "loader_options", "render_js", "rate_limit", "retry")
                   if (value := getattr(section, name)) is not None}
        return self.model_copy(update=updates) if updates else self

    @field_validator("output_subdirs")
    @classmethod
    def _relative_subdirs(cls, subdirs: dict[str, str]) -> dict[str, str]:
        for folder in subdirs.values():
            if Path(folder).is_absolute() or ".." in Path(folder).parts:
                raise ValueError(f"output_subdirs must be folders inside output_dir, got {folder!r}")
        return subdirs

    def output_subdir(self, record_type: str) -> str:
        return self.output_subdirs.get(record_type, "")

    def output_file_prefix(self, record_type: str) -> str:
        if record_type in self.outputs:
            return self.outputs[record_type]
        if record_type in FIXED_PREFIX_RECORD_TYPES:
            return record_type
        return f"{self.output_prefix or self.job_name}_{record_type}"


def _as_list(value: str | list[str]) -> list[str]:
    return [value] if isinstance(value, str) else list(value)


def list_jobs() -> list[str]:
    return sorted(path.stem for path in JOBS_DIR.glob("*.yaml"))


def load_job_config(job: str | Path) -> JobConfig:
    """Load a job by name (config/jobs/<name>.yaml) or by path to a YAML file."""
    path = Path(job)
    if path.suffix not in (".yaml", ".yml"):
        path = JOBS_DIR / f"{job}.yaml"
    if not path.is_file():
        raise ConfigError(f"Job config not found: {path}. Available jobs: {', '.join(list_jobs()) or 'none'}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: invalid YAML: {exc}") from exc
    try:
        return JobConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"{path}: invalid job config:\n{exc}") from exc


# ---------------------------------------------------------------- extraction rules


class ExtractionRules(BaseModel):
    default_region: str = "PK"
    provinces: dict[str, list[str]] = Field(default_factory=dict)
    cities: dict[str, str] = Field(default_factory=dict)
    address_keywords: list[str] = Field(default_factory=list)
    company_suffixes: list[str] = Field(default_factory=list)
    generic_domains: dict[str, list[str]] = Field(default_factory=dict)
    min_likely_domain_score: float = 45.0

    def with_overrides(self, overrides: ExtractionOverrides) -> ExtractionRules:
        generic = {category: list(domains) for category, domains in self.generic_domains.items()}
        for category, domains in overrides.extra_generic_domains.items():
            generic[category] = [*generic.get(category, []), *domains]
        return self.model_copy(update={
            "default_region": overrides.region or self.default_region,
            "cities": {**self.cities, **overrides.extra_cities},
            "address_keywords": [*self.address_keywords, *overrides.extra_address_keywords],
            "generic_domains": generic,
            "min_likely_domain_score": overrides.min_likely_domain_score,
        })

    def generic_category(self, domain: str) -> str | None:
        """Category of a generic (non-company) domain, matching the domain or any parent of it."""
        lookup = _generic_lookup(tuple((c, tuple(d)) for c, d in self.generic_domains.items()))
        parts = domain.lower().split(".")
        for i in range(len(parts) - 1):
            if category := lookup.get(".".join(parts[i:])):
                return category
        return None


@lru_cache(maxsize=8)
def _generic_lookup(items: tuple[tuple[str, tuple[str, ...]], ...]) -> dict[str, str]:
    return {domain.lower(): category for category, domains in items for domain in domains}


@lru_cache(maxsize=1)
def _base_rules() -> ExtractionRules:
    data = yaml.safe_load(EXTRACTION_RULES_FILE.read_text(encoding="utf-8")) or {}
    return ExtractionRules.model_validate(data)


def load_extraction_rules(overrides: ExtractionOverrides | None = None) -> ExtractionRules:
    rules = _base_rules()
    return rules.with_overrides(overrides) if overrides else rules
