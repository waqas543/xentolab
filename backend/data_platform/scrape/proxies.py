"""Outbound proxy pool. Health state lives in Redis so every fetcher shares it and stays stateless.

A domain sticks to one proxy for its whole crawl (consistent cookies and redirects); each retry moves to the
next one. A proxy that fails CONSECUTIVE_FAILURES times in a row cools down for COOLDOWN_SECONDS.
Credentials come from the proxies file and are only ever logged redacted (proxy ids, never URLs).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import redis.asyncio as aioredis

from ..observability import get_logger

log = get_logger(__name__)

CONSECUTIVE_FAILURES = 5
COOLDOWN_SECONDS = 600
COOLDOWN_KEY = "proxy:cooldown"     # zset: proxy id -> cooled-down until (epoch)
OK_KEY, FAIL_KEY = "proxy:ok", "proxy:fail"   # hashes: proxy id -> counts (for `status`)
CHECK_URL = "https://api.ipify.org"


@dataclass(frozen=True)
class Proxy:
    id: str
    host: str
    url: str = field(repr=False)        # includes credentials


def load(path: Path | None) -> list[Proxy]:
    """Read the provider's JSON ({"proxies": [{host, port, username, password}, ...]} or a bare list)."""
    if path is None:
        return []
    data = json.loads(path.read_text())
    entries = data.get("proxies", []) if isinstance(data, dict) else data
    proxies: dict[str, Proxy] = {}
    for entry in entries:
        host, port = entry.get("host") or entry.get("ip"), entry.get("port")
        if not host or not port:
            continue
        user, password = entry.get("username") or entry.get("user"), entry.get("password") or entry.get("pass")
        auth = f"{user}:{password}@" if user and password else ""
        address = f"{host}:{port}"
        proxy_id = "p" + hashlib.sha1(address.encode()).hexdigest()[:8]
        proxies.setdefault(address, Proxy(proxy_id, address, f"{entry.get('scheme', 'http')}://{auth}{address}"))
    return list(proxies.values())


class ProxyPool:
    def __init__(self, r: aioredis.Redis, proxies: list[Proxy]):
        self.r = r
        self.proxies = proxies

    async def choose(self, domain: str, attempt: int) -> Proxy | None:
        if not self.proxies:
            return None
        cooling = set(await self.r.zrangebyscore(COOLDOWN_KEY, time.time(), "+inf"))
        available = [p for p in self.proxies if p.id not in cooling] or self.proxies
        start = int(hashlib.sha1(domain.encode()).hexdigest()[:8], 16)
        return available[(start + attempt - 1) % len(available)]

    async def report(self, proxy: Proxy | None, ok: bool) -> None:
        if proxy is None:
            return
        fails_key = f"proxy:fails:{proxy.id}"
        if ok:
            async with self.r.pipeline(transaction=False) as pipe:
                pipe.delete(fails_key)
                pipe.hincrby(OK_KEY, proxy.id, 1)
                await pipe.execute()
            return
        await self.r.hincrby(FAIL_KEY, proxy.id, 1)
        if await self.r.incr(fails_key) >= CONSECUTIVE_FAILURES:
            await self.r.zadd(COOLDOWN_KEY, {proxy.id: time.time() + COOLDOWN_SECONDS})
            await self.r.delete(fails_key)
            log.warning("proxy cooling down", proxy=proxy.id, seconds=COOLDOWN_SECONDS)


async def check(proxies: list[Proxy], r: aioredis.Redis | None = None, timeout: float = 15) -> list[dict]:
    """Probe every proxy once. Healthy proxies are released from cooldown."""

    async def probe(proxy: Proxy) -> dict:
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(proxy=proxy.url, timeout=timeout) as client:
                response = await client.get(CHECK_URL)
            ok = response.status_code == 200
            result = {"id": proxy.id, "host": proxy.host, "ok": ok, "exit_ip": response.text.strip()[:45] if ok else None,
                      "ms": int((time.monotonic() - started) * 1000), "error": None if ok else f"HTTP {response.status_code}"}
        except httpx.HTTPError as error:
            result = {"id": proxy.id, "host": proxy.host, "ok": False, "exit_ip": None,
                      "ms": int((time.monotonic() - started) * 1000), "error": type(error).__name__}
        if r is not None and result["ok"]:
            await r.zrem(COOLDOWN_KEY, proxy.id)
        return result

    return await asyncio.gather(*(probe(p) for p in proxies))
