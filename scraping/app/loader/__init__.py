"""Page loaders. `create_loader` picks one from the job's `loader:` setting."""

from __future__ import annotations

from app.core.config import JobConfig, Settings
from app.fetch.rate_limiter import RateLimiter
from app.fetch.retry import RetryPolicy
from app.loader.base import PageLoader
from app.loader.http_loader import HttpLoader
from app.loader.js_loader import JsLoader
from app.loader.load_page_client import LoadPageClient
from app.loader.scrape_api_client import ScrapeApiClient

__all__ = ["HttpLoader", "JsLoader", "LoadPageClient", "PageLoader", "ScrapeApiClient", "create_loader"]


def create_loader(config: JobConfig, settings: Settings, *, limiter: RateLimiter | None = None) -> PageLoader:
    common = {"retry": RetryPolicy.from_config(config.retry), "limiter": limiter,
              "timeout": config.timeout_seconds, "render_js": config.render_js}
    if config.loader == "load_page":
        return LoadPageClient(endpoint=settings.load_page_url, api_key=settings.load_page_api_key, **common)
    if config.loader == "scrape_api":
        return ScrapeApiClient(endpoint=settings.scrape_api_url, api_key=settings.scrape_api_key,
                               options=config.loader_options, **common)
    if config.loader == "http":
        return HttpLoader(user_agent=settings.user_agent, **common)
    return JsLoader(user_agent=settings.user_agent, **common)
