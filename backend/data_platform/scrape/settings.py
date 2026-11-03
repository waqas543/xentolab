"""Scraping engine settings, read once from the environment (SCRAPE_*), on top of the platform settings."""

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from ..config import get_settings


def _env(name: str, default: str) -> str:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


@dataclass(frozen=True)
class ScrapeSettings:
    redis_url: str
    blob_dir: Path
    proxies_file: Path | None
    user_agent: str
    host_delay_seconds: float          # minimum gap between two requests to the same host
    fetch_timeout_seconds: float
    max_bytes: int                     # larger responses are cut off and reported as too_large
    max_redirects: int
    max_fetch_attempts: int            # transient fetch errors are retried this many times in total
    max_pages_per_domain: int
    max_depth: int
    max_deliveries: int                # a message that failed this often goes to the dead-letter stream
    claim_idle_ms: int                 # a message unacknowledged this long is handed to another worker
    blob_ttl_hours: float              # orphaned HTML older than this is swept
    ollama_url: str
    ollama_model: str
    llm_text_budget: int               # characters of page text sent to the model


@lru_cache
def get_scrape_settings() -> ScrapeSettings:
    platform = get_settings()
    # Proxy credentials are a secret: only a path in the environment, never a value in code or the database.
    proxies = platform.secret("PROXIES_FILE") or str(Path.home() / ".config/xentolab/proxies.json")
    return ScrapeSettings(
        redis_url=_env("REDIS_URL", "redis://localhost:6379/0"),
        blob_dir=Path(_env("SCRAPE_BLOB_DIR", str(platform.data_dir / "scrape" / "blobs"))),
        proxies_file=Path(proxies).expanduser() if Path(proxies).expanduser().exists() else None,
        user_agent=_env("SCRAPE_USER_AGENT", "XentoBot/1.0 (+https://xento.pk/bot)"),
        host_delay_seconds=float(_env("SCRAPE_HOST_DELAY_SECONDS", "2")),
        fetch_timeout_seconds=float(_env("SCRAPE_FETCH_TIMEOUT_SECONDS", "20")),
        max_bytes=int(_env("SCRAPE_MAX_BYTES", str(3 * 1024 * 1024))),
        max_redirects=int(_env("SCRAPE_MAX_REDIRECTS", "5")),
        max_fetch_attempts=int(_env("SCRAPE_MAX_FETCH_ATTEMPTS", "3")),
        max_pages_per_domain=int(_env("SCRAPE_MAX_PAGES_PER_DOMAIN", "4")),
        max_depth=int(_env("SCRAPE_MAX_DEPTH", "1")),
        max_deliveries=int(_env("SCRAPE_MAX_DELIVERIES", "5")),
        claim_idle_ms=int(float(_env("SCRAPE_CLAIM_IDLE_SECONDS", "120")) * 1000),
        blob_ttl_hours=float(_env("SCRAPE_BLOB_TTL_HOURS", "48")),
        ollama_url=_env("OLLAMA_URL", "http://localhost:11434"),
        ollama_model=_env("SCRAPE_LLM_MODEL", "qwen3:8b"),
        llm_text_budget=int(_env("SCRAPE_LLM_TEXT_BUDGET", "9000")),
    )
