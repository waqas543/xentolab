"""Direct HTTP loader (no JS rendering): cheapest option for static sites and APIs."""

from __future__ import annotations

import ssl
import time

import httpx
import truststore

from app.core.logging import get_logger
from app.core.models import PageResponse
from app.loader.base import PageLoader, elapsed_ms, raise_for_status, raise_for_transport

log = get_logger(__name__)


class HttpLoader(PageLoader):
    name = "http"

    def __init__(self, *, user_agent: str, client: httpx.AsyncClient | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        headers = {"User-Agent": user_agent, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                   "Accept-Language": "en-US,en;q=0.9"}
        # The OS trust store (unlike certifi) completes the incomplete certificate chains many sites serve.
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(self.timeout, connect=15.0),
                                                    follow_redirects=True, headers=headers,
                                                    verify=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT))
        self._owns_client = client is None
        self._warned = False

    async def _fetch_once(self, url: str, *, render_js: bool) -> PageResponse:
        if render_js and not self._warned:
            log.warning("loader.no_js", loader=self.name, hint="use loader: load_page or playwright to render JS")
            self._warned = True
        started = time.perf_counter()
        try:
            response = await self._client.get(url)
        except httpx.HTTPError as exc:
            raise_for_transport(exc, url)
        raise_for_status(response, url, self.retry.retry_on_status)
        return PageResponse(url=url, final_url=str(response.url), status_code=response.status_code,
                            html=response.text, content_type=response.headers.get("content-type"),
                            latency_ms=elapsed_ms(started), loader=self.name, render_js=False)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
