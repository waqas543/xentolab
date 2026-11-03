"""Bounded-concurrency work scheduler: N workers pull items from a shared (lazy) iterator.

Workers must handle their own expected errors; an unexpected exception cancels the remaining workers and
propagates, so bugs fail loudly instead of silently dropping work.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Iterator
from typing import TypeVar

T = TypeVar("T")


async def run_bounded(items: Iterable[T], worker: Callable[[T], Awaitable[None]], concurrency: int,
                      stop: asyncio.Event | None = None) -> None:
    iterator: Iterator[T] = iter(items)

    async def lane() -> None:
        # next() runs without awaiting, so workers never receive the same item.
        while not (stop and stop.is_set()):
            try:
                item = next(iterator)
            except StopIteration:
                return
            await worker(item)

    async with asyncio.TaskGroup() as group:
        for _ in range(max(1, concurrency)):
            group.create_task(lane())
