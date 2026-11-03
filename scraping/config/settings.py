"""Environment settings (secrets and endpoints), read from the process environment and `.env`.

Secrets live only here: never in job YAML, logs or output records.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = PROJECT_ROOT / "config"
JOBS_DIR = CONFIG_DIR / "jobs"
EXTRACTION_RULES_FILE = CONFIG_DIR / "extraction_rules.yaml"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    load_page_url: str = "http://load-page"
    load_page_api_key: SecretStr | None = None
    scrape_api_url: str = "http://127.0.0.1:8000/v1/scrape"
    scrape_api_key: SecretStr | None = None
    ollama_url: str = "http://localhost:11434"
    user_agent: str = "Mozilla/5.0 (compatible; XentoScraper/0.1; +https://xento.pk)"
    log_level: str = "INFO"
    log_dir: Path = Field(default=PROJECT_ROOT / "output" / "logs")


@lru_cache
def get_settings() -> Settings:
    return Settings()
