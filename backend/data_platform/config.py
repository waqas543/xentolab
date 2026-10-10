"""Platform settings, read once from the environment.

Secrets (database passwords, provider API keys) only ever come from the environment
and are never written to the control plane, logs or API responses.
"""

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    control_dsn: str
    serving_dsn: str
    data_dir: Path
    auth_mode: str                      # "dev" | "api_key"
    min_free_disk_bytes: int
    worker_poll_seconds: float
    heartbeat_seconds: float
    stale_run_seconds: float
    http_user_agent: str
    provider_secrets: dict[str, str] = field(default_factory=dict, repr=False)

    def secret(self, name: str) -> str | None:
        """Provider credential by name, e.g. secret("OPENCORPORATES_API_TOKEN")."""
        return self.provider_secrets.get(name)


@lru_cache
def get_settings() -> Settings:
    default_dsn = _env("DATABASE_URL", "dbname=ds_companies")
    data_dir = Path(_env("DATA_PLATFORM_DATA_DIR", str(REPO_ROOT / "data"))).expanduser().resolve()

    auth_mode = _env("DATA_PLATFORM_AUTH_MODE", "dev")
    if auth_mode not in ("dev", "api_key"):
        raise ValueError("DATA_PLATFORM_AUTH_MODE must be 'dev' or 'api_key'")

    # Any DP_SECRET_<NAME> variable is exposed to adapters as secret("<NAME>").
    secrets = {key.removeprefix("DP_SECRET_"): value for key, value in os.environ.items() if key.startswith("DP_SECRET_")}

    return Settings(
        control_dsn=_env("CONTROL_DATABASE_URL", default_dsn),
        serving_dsn=_env("SERVING_DATABASE_URL", default_dsn),
        data_dir=data_dir,
        auth_mode=auth_mode,
        min_free_disk_bytes=int(_env("DATA_PLATFORM_MIN_FREE_DISK_MB", "500")) * 1024 * 1024,
        worker_poll_seconds=float(_env("DATA_PLATFORM_WORKER_POLL_SECONDS", "2")),
        heartbeat_seconds=float(_env("DATA_PLATFORM_HEARTBEAT_SECONDS", "10")),
        stale_run_seconds=float(_env("DATA_PLATFORM_STALE_RUN_SECONDS", "120")),
        http_user_agent=_env("DATA_PLATFORM_USER_AGENT", "osm-companies-data-platform/1.0 (local)"),
        provider_secrets=secrets,
    )
