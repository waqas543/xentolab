"""Client for the self-hosted scrape API, which renders pages in a real browser.

    POST $SCRAPE_API_URL   Authorization: Bearer $SCRAPE_API_KEY
    {"url", "render_js", "wait_until", "timeout_ms", "response_format": "html", "block_resources", "wait_for"}
    -> 200 {"outcome": "success" | "target_rate_limited" | ..., "content": "<html>", "final_url", "status_code"}
    -> 4xx/5xx {"error": {"code": "TARGET_CHALLENGE" | "WAIT_CONDITION_TIMEOUT" | ..., "retryable", "retry_after_s"}}

A bot challenge or rate limit from the target becomes a 429 TransientLoaderError, so the rate limiter pauses
every request sharing it before the retry. `networkidle` never settles on pages like Google's (they keep
polling); `load` is the default. The API accepts at most 60 s for a synchronous request, so the job's
`timeout_seconds` is capped there.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import orjson
from pydantic import SecretStr

from app.core.config import LoaderOptions
from app.core.exceptions import ConfigError, LoaderError, TransientLoaderError
from app.core.models import PageResponse
from app.loader.base import PageLoader, elapsed_ms, raise_for_transport

# Error codes that mean the target is pushing back: back off like a 429.
_BLOCKED_CODES = {"TARGET_CHALLENGE", "TARGET_RATE_LIMITED"}
_BLOCKED_OUTCOMES = {"target_rate_limited"}
_DENIED_OUTCOMES = {"target_auth_required", "target_access_denied"}
MAX_SYNC_TIMEOUT_MS = 60_000


class ScrapeApiClient(PageLoader):
    name = "scrape-api"

    def __init__(self, *, endpoint: str, api_key: SecretStr | str | None, options: LoaderOptions | None = None,
                 client: httpx.AsyncClient | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        key = api_key.get_secret_value() if isinstance(api_key, SecretStr) else api_key
        if not key:
            raise ConfigError("SCRAPE_API_KEY is not set: add it to .env (see .env.example)")
        if not endpoint:
            raise ConfigError("SCRAPE_API_URL is not set")
        self.endpoint = endpoint
        self.options = options or LoaderOptions()
        self._headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        # The API's own deadline (timeout_ms) includes queueing; give the HTTP call a little more.
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(self.timeout + 15, connect=15.0))
        self._owns_client = client is None

    def payload(self, url: str, render_js: bool) -> dict[str, Any]:
        options = self.options
        payload: dict[str, Any] = {"url": url, "render_js": render_js, "wait_until": options.wait_until,
                                   "timeout_ms": min(int(self.timeout * 1000), MAX_SYNC_TIMEOUT_MS),
                                   "response_format": "html"}
        if options.block_resources:
            payload["block_resources"] = list(options.block_resources)
        if options.wait_for_selector:
            wait_for: dict[str, Any] = {"selector": options.wait_for_selector, "state": "attached"}
            if options.wait_for_timeout_ms:
                wait_for["timeout_ms"] = options.wait_for_timeout_ms
            payload["wait_for"] = wait_for
        return payload

    async def _fetch_once(self, url: str, *, render_js: bool) -> PageResponse:
        started = time.perf_counter()
        try:
            response = await self._client.post(self.endpoint, content=orjson.dumps(self.payload(url, render_js)),
                                               headers=self._headers)
        except httpx.HTTPError as exc:
            raise_for_transport(exc, url)
        latency = elapsed_ms(started)
        body = _json(response)
        if response.status_code >= 400 or "error" in body:
            self._raise_api_error(response, body, url)

        outcome = body.get("outcome")
        status = body.get("status_code") or 200
        retry_after = body.get("retry_after_s")
        if outcome in _BLOCKED_OUTCOMES:
            raise TransientLoaderError(f"target rate limited ({outcome}, HTTP {status})", status_code=429,
                                       retry_after=retry_after)
        if outcome in _DENIED_OUTCOMES:
            raise LoaderError(f"target refused access ({outcome})", url=url, status_code=status)
        if outcome != "success":
            if status in self.retry.retry_on_status:
                raise TransientLoaderError(f"target returned HTTP {status} ({outcome})", status_code=status,
                                           retry_after=retry_after)
            raise LoaderError(f"target returned HTTP {status} ({outcome})", url=url, status_code=status)

        html = body.get("content")
        if not isinstance(html, str) or not html.strip():
            raise TransientLoaderError("scrape API returned an empty page")
        return PageResponse(url=url, final_url=body.get("final_url") or url, status_code=status, html=html,
                            content_type="text/html", latency_ms=latency, loader=self.name, render_js=render_js)

    def _raise_api_error(self, response: httpx.Response, body: dict[str, Any], url: str) -> None:
        error = body.get("error") if isinstance(body.get("error"), dict) else {}
        code = error.get("code") or f"HTTP_{response.status_code}"
        message = f"{code}: {error.get('message') or response.text[:200].strip()}"
        retry_after = error.get("retry_after_s")
        details = error.get("details") if isinstance(error.get("details"), dict) else {}
        if code in _BLOCKED_CODES:
            raise TransientLoaderError(message, status_code=429, retry_after=retry_after)
        if response.status_code in (401, 403):
            raise LoaderError(f"scrape API rejected the request (check SCRAPE_API_KEY): {message}", url=url,
                              status_code=response.status_code)
        if (error.get("retryable") or code.endswith("TIMEOUT")
                or response.status_code in self.retry.retry_on_status):
            raise TransientLoaderError(message, status_code=details.get("status_code") or response.status_code,
                                       retry_after=retry_after)
        raise LoaderError(message, url=url, status_code=details.get("status_code") or response.status_code)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        data = orjson.loads(response.content)
    except orjson.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}
