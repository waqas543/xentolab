"""Job processing on Redis Streams: consumer groups, acknowledgement, redelivery and a dead-letter stream.

Delivery is at-least-once. A worker acknowledges a message only after handling it; a message whose worker
died (or whose handler raised) stays pending and is reclaimed by any worker of the group once it has been
idle for `claim_idle_ms`, which doubles as the retry backoff. After `max_deliveries` it is moved to the
dead-letter stream. Acknowledged messages are deleted, so streams only hold outstanding work.
"""

from __future__ import annotations

import asyncio
import signal
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

import redis
import redis.asyncio as aioredis
from redis.exceptions import ResponseError

from ..errors import PermanentError
from ..observability import WORKER_ID, get_logger, log_context, redact
from .messages import DLQ, RETRY, Message
from .settings import get_scrape_settings

log = get_logger(__name__)

CLAIM_EVERY_SECONDS = 10
READ_BLOCK_MS = 1000

# Atomically move due retries (score <= now) from the retry set onto the requests stream.
_MOVE_DUE = """
local items = redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', ARGV[1], 'LIMIT', 0, tonumber(ARGV[2]))
for _, item in ipairs(items) do
  redis.call('XADD', KEYS[2], '*', 'm', item)
  redis.call('ZREM', KEYS[1], item)
end
return #items
"""


def connect() -> aioredis.Redis:
    return aioredis.from_url(get_scrape_settings().redis_url, decode_responses=True)


def connect_sync() -> redis.Redis:
    return redis.from_url(get_scrape_settings().redis_url, decode_responses=True)


async def ensure_group(r: aioredis.Redis, stream: str, group: str) -> None:
    try:
        await r.xgroup_create(stream, group, id="0", mkstream=True)
    except ResponseError as error:
        if "BUSYGROUP" not in str(error):
            raise


async def publish(r: aioredis.Redis, *messages: Message) -> None:
    if not messages:
        return
    async with r.pipeline(transaction=False) as pipe:
        for message in messages:
            pipe.xadd(message.stream, {"m": message.to_json()})
        await pipe.execute()


def publish_sync(r: redis.Redis, *messages: Message) -> None:
    if not messages:
        return
    with r.pipeline(transaction=False) as pipe:
        for message in messages:
            pipe.xadd(message.stream, {"m": message.to_json()})
        pipe.execute()


async def schedule_retry(r: aioredis.Redis, message: Message, due_epoch: float) -> None:
    await r.zadd(RETRY, {message.to_json(): due_epoch})


def move_due_retries(r: redis.Redis, target_stream: str, limit: int = 500) -> int:
    return int(r.eval(_MOVE_DUE, 2, RETRY, target_stream, time.time(), limit))


@dataclass
class Delivery:
    id: str
    raw: str
    deliveries: int


class StreamWorker:
    """Consumes one stream as a member of `group`.

    Give either `handle` (one message at a time, up to `concurrency` in flight; for I/O-bound agents) or
    `handle_batch` (up to `batch_size` messages per call; for the writer). Handlers raise to signal failure:
    PermanentError dead-letters the message at once, anything else leaves it for redelivery.
    """

    def __init__(self, r: aioredis.Redis, stream: str, group: str, *,
                 handle: Callable[[Delivery], Awaitable[None]] | None = None,
                 handle_batch: Callable[[list[Delivery]], Awaitable[None]] | None = None,
                 concurrency: int = 1, batch_size: int = 50):
        assert (handle is None) != (handle_batch is None), "give exactly one of handle / handle_batch"
        settings = get_scrape_settings()
        self.r, self.stream, self.group = r, stream, group
        self.handle, self.handle_batch = handle, handle_batch
        self.concurrency, self.batch_size = concurrency, batch_size
        self.consumer = WORKER_ID
        self.claim_idle_ms = settings.claim_idle_ms
        self.max_deliveries = settings.max_deliveries
        self.handled = 0

    async def run(self, stop: asyncio.Event) -> None:
        await ensure_group(self.r, self.stream, self.group)
        tasks: set[asyncio.Task] = set()
        last_claim = 0.0
        with log_context(stream=self.stream, group=self.group):
            log.info("consumer started", concurrency=self.concurrency)
            while not stop.is_set():
                room = self.batch_size if self.handle_batch else self.concurrency - len(tasks)
                if room <= 0:
                    await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    continue
                batch: list[Delivery] = []
                if time.monotonic() - last_claim >= CLAIM_EVERY_SECONDS:
                    last_claim = time.monotonic()
                    batch = await self._claim(room)
                if not batch:
                    batch = await self._read(room)
                if not batch:
                    continue
                if self.handle_batch:
                    await self._run_batch(batch)
                else:
                    for delivery in batch:
                        task = asyncio.create_task(self._run_one(delivery))
                        tasks.add(task)
                        task.add_done_callback(tasks.discard)
            if tasks:
                # Unfinished messages stay pending and are reclaimed by another worker.
                await asyncio.wait(tasks, timeout=30)
            await self._leave_group()
            log.info("consumer stopped", handled=self.handled)

    # -- reading -----------------------------------------------------------------------

    async def _read(self, count: int) -> list[Delivery]:
        response = await self.r.xreadgroup(self.group, self.consumer, {self.stream: ">"}, count=count,
                                           block=READ_BLOCK_MS)
        return [Delivery(id, entry.get("m", ""), 1) for _, entries in response or [] for id, entry in entries]

    async def _claim(self, count: int) -> list[Delivery]:
        """Take over messages another worker received but never acknowledged (it crashed, or its handler failed)."""
        _, claimed, *_ = await self.r.xautoclaim(self.stream, self.group, self.consumer,
                                                 min_idle_time=self.claim_idle_ms, start_id="0-0", count=count)
        deliveries = []
        for id, entry in claimed:
            info = await self.r.xpending_range(self.stream, self.group, min=id, max=id, count=1)
            times = info[0]["times_delivered"] if info else 1
            delivery = Delivery(id, (entry or {}).get("m", ""), times)
            if times > self.max_deliveries:
                await self._dead_letter(delivery, f"gave up after {times - 1} deliveries")
            else:
                deliveries.append(delivery)
        if deliveries:
            log.warning("reclaimed messages", count=len(deliveries))
        return deliveries

    # -- handling ----------------------------------------------------------------------

    async def _run_one(self, delivery: Delivery) -> None:
        try:
            await self.handle(delivery)
        except PermanentError as error:
            await self._dead_letter(delivery, str(error))
        except Exception as error:
            log.exception("handler failed; message will be redelivered", message_id=delivery.id,
                          deliveries=delivery.deliveries, error=redact(str(error)))
        else:
            await self._ack([delivery.id])

    async def _run_batch(self, batch: list[Delivery]) -> None:
        try:
            await self.handle_batch(batch)
        except Exception:
            if len(batch) == 1:
                return await self._run_single_batch(batch[0])
            # Isolate the bad message: the others succeed on their own.
            log.warning("batch failed; retrying messages one by one", size=len(batch))
            for delivery in batch:
                await self._run_single_batch(delivery)
            return
        await self._ack([d.id for d in batch])

    async def _run_single_batch(self, delivery: Delivery) -> None:
        try:
            await self.handle_batch([delivery])
        except PermanentError as error:
            await self._dead_letter(delivery, str(error))
        except Exception as error:
            log.exception("handler failed; message will be redelivered", message_id=delivery.id,
                          deliveries=delivery.deliveries, error=redact(str(error)))
        else:
            await self._ack([delivery.id])

    async def _ack(self, ids: list[str]) -> None:
        async with self.r.pipeline(transaction=True) as pipe:
            pipe.xack(self.stream, self.group, *ids)
            pipe.xdel(self.stream, *ids)
            await pipe.execute()
        self.handled += len(ids)

    async def _dead_letter(self, delivery: Delivery, reason: str) -> None:
        log.error("dead-lettered message", message_id=delivery.id, reason=redact(reason))
        await self.r.xadd(DLQ, {"stream": self.stream, "group": self.group, "id": delivery.id, "m": delivery.raw,
                                "error": redact(reason)[:2000], "deliveries": delivery.deliveries,
                                "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        await self._ack([delivery.id])

    async def _leave_group(self) -> None:
        """Remove this consumer from the group on a clean exit, unless it still owns pending messages."""
        try:
            info = await self.r.xpending_range(self.stream, self.group, min="-", max="+", count=1,
                                               consumername=self.consumer)
            if not info:
                await self.r.xgroup_delconsumer(self.stream, self.group, self.consumer)
        except Exception:
            log.exception("could not leave consumer group")


def run_until_signalled(*make_coroutines: Callable[[asyncio.Event], Awaitable[None]]) -> None:
    """Run agent coroutines until SIGINT/SIGTERM, then let them finish their in-flight work."""

    async def main():
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        tasks = [asyncio.create_task(make(stop)) for make in make_coroutines]
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
        stop.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        for task in done:
            if task.exception():
                raise task.exception()

    asyncio.run(main())
