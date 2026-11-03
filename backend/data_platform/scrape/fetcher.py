"""Fetcher agent: FetchRequest -> HTTP GET (via proxy, politely) -> blob store -> FetchResult.

Stateless: the attempt count travels in the message, rate limits / proxy health / robots.txt live in Redis,
the body goes to the blob store. It never parses and never touches Postgres. Transient failures are retried
through the delayed retry set (with the next proxy); final outcomes, good or bad, go to scrape:fetched.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx
import redis.asyncio as aioredis

from ..config import get_settings
from ..errors import TransientError
from ..observability import get_logger
from . import blobstore, politeness, sites
from .messages import REQUESTS, FetchRequest, FetchResult
from .proxies import Proxy, ProxyPool, load
from .settings import get_scrape_settings
from .streams import Delivery, StreamWorker, publish, schedule_retry

log = get_logger(__name__)

GROUP = "fetcher"
TRANSIENT = {"timeout", "connect", "dns", "proxy", "network", "http_5xx", "rate_limited"}
HOST_WAIT_SECONDS = 30            # longer than this and the request goes back to the retry set instead
RETRY_BASE_SECONDS = 30
HTML_TYPES = ("text/html", "application/xhtml")
_PROXY_STATUS = re.compile(r"^(\d{3})\b")


def classify_status(status: int) -> str | None:
    if status < 400:
        return None
    if status == 429:
        return "rate_limited"
    if status == 451:
        return "geo_block"
    return "http_5xx" if status >= 500 else "http_4xx"


class Fetcher:
    def __init__(self, r: aioredis.Redis):
        self.r = r
        self.settings = get_scrape_settings()
        self.pool = ProxyPool(r, load(self.settings.proxies_file))
        self.slots = politeness.HostSlots(r, self.settings.host_delay_seconds,
                                          lock_seconds=2 * self.settings.fetch_timeout_seconds + 15)
        self.min_free_bytes = get_settings().min_free_disk_bytes
        self._clients: dict[tuple[str | None, bool], httpx.AsyncClient] = {}

    def client(self, proxy: Proxy | None, verify: bool = True) -> httpx.AsyncClient:
        key = (proxy.id if proxy else None, verify)
        if key not in self._clients:
            s = self.settings
            self._clients[key] = httpx.AsyncClient(
                proxy=proxy.url if proxy else None, verify=verify, follow_redirects=True,
                max_redirects=s.max_redirects,
                timeout=httpx.Timeout(s.fetch_timeout_seconds, connect=min(10.0, s.fetch_timeout_seconds)),
                limits=httpx.Limits(max_connections=50, max_keepalive_connections=10),
                headers={"User-Agent": s.user_agent, "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
                         "Accept-Language": "en,ur;q=0.8"})
        return self._clients[key]

    async def close(self) -> None:
        for client in self._clients.values():
            await client.aclose()

    # -- job ---------------------------------------------------------------------------

    async def handle(self, delivery: Delivery) -> None:
        request = FetchRequest.from_json(delivery.raw)
        if blobstore.free_bytes() < self.min_free_bytes:
            # Not the request's fault: leave it pending; it is redelivered once there is room again.
            log.error("low disk space; pausing fetches", free_bytes=blobstore.free_bytes())
            await asyncio.sleep(30)
            raise TransientError("insufficient disk space for blobs")

        host = urlsplit(request.url).hostname or request.domain
        token = await self.slots.acquire(host, HOST_WAIT_SECONDS)
        if token is None:            # host busy: come back later without holding a worker slot or an attempt
            await schedule_retry(self.r, request, time.time() + 3 * self.settings.host_delay_seconds)
            return
        site = sites.get(request.site)
        try:
            proxy = await self.pool.choose(request.domain, request.attempt)
            result = await self.fetch(request, proxy)
        finally:
            await self.slots.release(host, token, site.host_delay_seconds)

        if result.error_class in TRANSIENT and request.attempt < self.settings.max_fetch_attempts:
            request.attempt += 1
            due = time.time() + RETRY_BASE_SECONDS * 2 ** (request.attempt - 2)
            await schedule_retry(self.r, request, due)
            log.info("fetch will be retried", url=request.url, error_class=result.error_class, attempt=request.attempt)
            return
        await publish(self.r, result)

    async def fetch(self, request: FetchRequest, proxy: Proxy | None) -> FetchResult:
        result = FetchResult(request_id=request.request_id, campaign_id=request.campaign_id, domain=request.domain,
                             country=request.country, url=request.url, kind=request.kind, depth=request.depth,
                             attempt=request.attempt, fetched_at=datetime.now(timezone.utc).isoformat(),
                             proxy_id=proxy.id if proxy else None, site=request.site)
        started = time.monotonic()
        try:
            if not await self.robots_allow(request.url, proxy):
                result.error_class, result.error_message = "robots_denied", "disallowed by robots.txt"
                return result
            try:
                await self._get(result, self.client(proxy))
            except httpx.ConnectError as error:
                if "CERTIFICATE_VERIFY_FAILED" not in str(error) and "SSL" not in str(error):
                    raise
                # Many small sites have broken certificates; the content is public either way.
                await self._get(result, self.client(proxy, verify=False))
            await self.pool.report(proxy, ok=True)
        except httpx.ProxyError as error:
            # "502 Bad Gateway" etc. on CONNECT means the proxy could not reach the site: not the proxy's fault.
            status = _PROXY_STATUS.match(str(error))
            if status and status.group(1).startswith("5"):
                result.error_class = "connect"
            else:
                result.error_class = "proxy"
                await self.pool.report(proxy, ok=False)
            result.error_message = str(error)[:500]
        except httpx.TimeoutException as error:
            result.error_class, result.error_message = "timeout", type(error).__name__
        except httpx.TooManyRedirects as error:
            result.error_class, result.error_message = "redirect_loop", str(error)[:500]
        except httpx.ConnectError as error:
            message = str(error)
            if proxy is not None and "SSL" not in message:
                result.error_class = "proxy"           # could not reach the proxy itself
                await self.pool.report(proxy, ok=False)
            elif "nodename" in message or "Name or service" in message or "getaddrinfo" in message:
                result.error_class = "dns"
            else:
                result.error_class = "tls" if "SSL" in message else "connect"
            result.error_message = message[:500]
        except (httpx.NetworkError, httpx.RemoteProtocolError) as error:
            # Connection reset or cut off mid-response: worth another try through the next proxy.
            result.error_class, result.error_message = "network", f"{type(error).__name__}: {error}"[:500]
        except httpx.HTTPError as error:
            result.error_class, result.error_message = "other", f"{type(error).__name__}: {error}"[:500]
        finally:
            result.elapsed_ms = int((time.monotonic() - started) * 1000)
        return result

    async def _get(self, result: FetchResult, client: httpx.AsyncClient) -> None:
        async with client.stream("GET", result.url, headers=sites.get(result.site).headers) as response:
            result.status = response.status_code
            result.final_url = str(response.url)
            result.content_type = response.headers.get("content-type", "")[:200]
            if error := classify_status(response.status_code):
                result.error_class, result.error_message = error, f"HTTP {response.status_code}"
                return
            if result.content_type and not result.content_type.lower().startswith(HTML_TYPES):
                result.error_class, result.error_message = "not_html", result.content_type
                return
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) >= self.settings.max_bytes:
                    break                               # the head of a huge page is still worth parsing
        result.bytes = len(body)
        result.sha256 = hashlib.sha256(body).hexdigest()
        result.blob_key = blobstore.put(result.request_id, bytes(body))

    async def robots_allow(self, url: str, proxy: Proxy | None) -> bool:
        parts = urlsplit(url)
        host = parts.hostname or ""
        body = await politeness.cached_robots(self.r, host)
        if body is None:
            available = True
            try:
                response = await self.client(proxy, verify=False).get(f"{parts.scheme}://{parts.netloc}/robots.txt",
                                                                       timeout=10)
                if response.status_code == 200 and "html" not in response.headers.get("content-type", ""):
                    body = response.text
                else:
                    body = ""                           # 4xx: no rules. 5xx: treat as unavailable.
                    available = response.status_code < 500
            except httpx.HTTPError:
                body, available = "", False
            await politeness.cache_robots(self.r, host, body, available)
        return politeness.robots_allows(body, self.settings.user_agent, url)


async def run(stop: asyncio.Event, concurrency: int = 64) -> None:
    from .streams import connect

    r = connect()
    fetcher = Fetcher(r)
    log.info("fetcher ready", proxies=len(fetcher.pool.proxies), concurrency=concurrency)
    try:
        await StreamWorker(r, REQUESTS, GROUP, handle=fetcher.handle, concurrency=concurrency).run(stop)
    finally:
        await fetcher.close()
        await r.aclose()
