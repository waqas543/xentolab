"""Client for the hosted `load-page` rendering API (ScrapingBee-style).

    POST $LOAD_PAGE_URL   X-API-KEY: $LOAD_PAGE_API_KEY
    {"url": "<target>", "render_js": true, "json_return": false}   ->   raw HTML body

Replaceable: anything implementing PageLoader (e.g. a self-hosted renderer) can take its place via the job's
`loader:` setting.
"""

from __future__ import annotations

import time

import httpx
import orjson
from pydantic import SecretStr

from app.core.exceptions import ConfigError, LoaderError, TransientLoaderError
from app.core.models import PageResponse
from app.loader.base import PageLoader, elapsed_ms, raise_for_status, raise_for_transport

# Headers a rendering API commonly uses to report the target page's own status and final URL.
_FINAL_URL_HEADERS = ("x-final-url", "x-resolved-url", "x-url", "spb-resolved-url")
_TARGET_STATUS_HEADERS = ("x-status-code", "x-target-status", "x-original-status", "spb-initial-status-code")
_HTML_KEYS = ("html", "content", "body", "data")


class LoadPageClient(PageLoader):
    name = "load-page"

    def __init__(self, *, endpoint: str, api_key: SecretStr | str | None, client: httpx.AsyncClient | None = None,
                 **kwargs) -> None:
        super().__init__(**kwargs)
        key = api_key.get_secret_value() if isinstance(api_key, SecretStr) else api_key
        if not key:
            raise ConfigError("LOAD_PAGE_API_KEY is not set: copy .env.example to .env and fill it in")
        if not endpoint:
            raise ConfigError("LOAD_PAGE_URL is not set")
        self.endpoint = endpoint
        self._headers = {"X-API-KEY": key, "Content-Type": "application/json", "Accept": "text/html,*/*"}
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(self.timeout, connect=15.0))
        self._owns_client = client is None

    async def _fetch_once(self, url: str, *, render_js: bool) -> PageResponse:
        payload = {"url": url, "render_js": render_js, "json_return": False}
        started = time.perf_counter()
        try:
            response = await self._client.post(self.endpoint, content=orjson.dumps(payload), headers=self._headers)
        except httpx.HTTPError as exc:
            raise_for_transport(exc, url)
        latency = elapsed_ms(started)
        raise_for_status(response, url, self.retry.retry_on_status)

        html = self._extract_html(response, url)
        status = _int_header(response, _TARGET_STATUS_HEADERS) or response.status_code
        if status in self.retry.retry_on_status:
            raise TransientLoaderError(f"target returned HTTP {status}", status_code=status)
        if status >= 400:
            raise LoaderError(f"target returned HTTP {status}", url=url, status_code=status)
        final_url = next((response.headers[h] for h in _FINAL_URL_HEADERS if response.headers.get(h)), url)
        return PageResponse(url=url, final_url=final_url, status_code=status, html=html,
                            content_type=response.headers.get("content-type"), latency_ms=latency,
                            loader=self.name, render_js=render_js)

    @staticmethod
    def _extract_html(response: httpx.Response, url: str) -> str:
        body = response.text
        if not body.strip():
            raise TransientLoaderError("loader returned an empty body")
        if "json" not in (response.headers.get("content-type") or ""):
            return body
        # json_return=false should give raw HTML; tolerate a JSON envelope or a JSON error message anyway.
        try:
            data = orjson.loads(body)
        except orjson.JSONDecodeError:
            return body
        if isinstance(data, dict):
            for key in _HTML_KEYS:
                if isinstance(data.get(key), str) and data[key].strip():
                    return data[key]
            message = data.get("error") or data.get("message") or data.get("detail")
            if message:
                raise TransientLoaderError(f"loader error: {str(message)[:200]}")
        raise LoaderError("loader returned JSON without HTML", url=url)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _int_header(response: httpx.Response, names: tuple[str, ...]) -> int | None:
    for name in names:
        value = response.headers.get(name)
        if value and value.strip().isdigit():
            return int(value)
    return None
