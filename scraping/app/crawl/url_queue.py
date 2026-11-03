"""Crawl frontier: breadth-first queue of URLs with de-duplication on the normalized URL."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from app.core.utils import normalize_url


@dataclass(frozen=True)
class CrawlItem:
    url: str
    depth: int
    parent: str | None = None


class URLQueue:
    def __init__(self, max_size: int = 10_000) -> None:
        self._queue: deque[CrawlItem] = deque()
        self._seen: set[str] = set()
        self.max_size = max_size

    def push(self, url: str, depth: int, parent: str | None = None) -> bool:
        """Queue a URL unless it was already seen or the queue is full. Returns whether it was added."""
        key = normalize_url(url)
        if key in self._seen or len(self._queue) >= self.max_size:
            return False
        self._seen.add(key)
        self._queue.append(CrawlItem(url=url, depth=depth, parent=parent))
        return True

    def pop(self) -> CrawlItem | None:
        return self._queue.popleft() if self._queue else None

    def seen(self, url: str) -> bool:
        return normalize_url(url) in self._seen

    def __len__(self) -> int:
        return len(self._queue)
