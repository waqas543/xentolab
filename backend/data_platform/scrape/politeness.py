"""Per-host politeness shared by all fetchers through Redis: one request in flight per host, a minimum delay
after each one, and a robots.txt cache. Proxies do not change any of this; the limits protect the site.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from urllib.robotparser import RobotFileParser

import redis.asyncio as aioredis

ROBOTS_TTL_SECONDS = 24 * 3600
ROBOTS_UNAVAILABLE_TTL_SECONDS = 3600
ROBOTS_MAX_BYTES = 500_000

# Release the lock only if we still own it (it may have expired and been taken by another fetcher).
_RELEASE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end
return 0
"""


class HostSlots:
    def __init__(self, r: aioredis.Redis, delay_seconds: float, lock_seconds: float):
        self.r = r
        self.delay = delay_seconds
        self.lock_ms = int(lock_seconds * 1000)

    async def acquire(self, host: str, max_wait: float) -> str | None:
        """Wait until the host is free and rested. Returns a token, or None if that takes longer than max_wait."""
        deadline = time.monotonic() + max_wait
        token = uuid.uuid4().hex
        while True:
            next_at = float(await self.r.get(f"host:next:{host}") or 0)
            now = time.time()
            if now >= next_at and await self.r.set(f"host:lock:{host}", token, nx=True, px=self.lock_ms):
                return token
            wait = max(next_at - now, 0.25)
            if time.monotonic() + wait > deadline:
                return None
            await asyncio.sleep(min(wait, 1.0))

    async def release(self, host: str, token: str, delay: float | None = None) -> None:
        delay = self.delay if delay is None else delay
        await self.r.set(f"host:next:{host}", time.time() + delay, ex=max(int(delay * 10), 60))
        await self.r.eval(_RELEASE, 1, f"host:lock:{host}", token)


async def cached_robots(r: aioredis.Redis, host: str) -> str | None:
    """robots.txt body from the cache ("" means allow everything), or None if not cached."""
    return await r.get(f"robots:{host}")


async def cache_robots(r: aioredis.Redis, host: str, body: str, available: bool) -> None:
    ttl = ROBOTS_TTL_SECONDS if available else ROBOTS_UNAVAILABLE_TTL_SECONDS
    await r.set(f"robots:{host}", body[:ROBOTS_MAX_BYTES], ex=ttl)


def robots_allows(body: str, user_agent: str, url: str) -> bool:
    if not body.strip():
        return True
    parser = RobotFileParser()
    parser.parse(body.splitlines())
    return parser.can_fetch(user_agent.split("/")[0], url)
