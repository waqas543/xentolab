"""Raw HTML between the fetcher and the writer: zstd-compressed files, one per fetch.

Keys are derived from the request id (not the content hash), so two pages with identical bodies never share
a file and deleting one cannot break the other. The writer deletes a blob once its parsed page is committed;
`sweep` removes leftovers (dead-lettered or abandoned) after the TTL.
"""

from __future__ import annotations

import os
import shutil
import time
from compression import zstd
from pathlib import Path

from ..observability import get_logger
from .settings import get_scrape_settings

log = get_logger(__name__)
SUFFIX = ".html.zst"


def _root() -> Path:
    return get_scrape_settings().blob_dir


def key_for(request_id: str) -> str:
    return f"{request_id[:2]}/{request_id}{SUFFIX}"


def put(request_id: str, body: bytes) -> str:
    key = key_for(request_id)
    path = _root() / key
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".tmp{os.getpid()}")
    tmp.write_bytes(zstd.compress(body, level=6))
    tmp.replace(path)                     # atomic: readers never see a partial file
    return key


def get(key: str) -> bytes:
    return zstd.decompress((_root() / key).read_bytes())


def delete(*keys: str | None) -> None:
    for key in keys:
        if key:
            (_root() / key).unlink(missing_ok=True)


def free_bytes() -> int:
    root = _root()
    root.mkdir(parents=True, exist_ok=True)
    return shutil.disk_usage(root).free


def sweep(ttl_hours: float | None = None) -> tuple[int, int]:
    """Delete blobs older than the TTL. Returns (files, bytes) removed."""
    cutoff = time.time() - 3600 * (ttl_hours if ttl_hours is not None else get_scrape_settings().blob_ttl_hours)
    files = size = 0
    root = _root()
    if not root.exists():
        return 0, 0
    for path in root.glob(f"*/*{SUFFIX}*"):
        try:
            stat = path.stat()
            if stat.st_mtime < cutoff:
                path.unlink()
                files, size = files + 1, size + stat.st_size
        except FileNotFoundError:
            continue
    if files:
        log.info("swept stale blobs", files=files, bytes=size)
    return files, size


def usage() -> tuple[int, int]:
    """(files, bytes) currently stored."""
    root = _root()
    if not root.exists():
        return 0, 0
    sizes = [p.stat().st_size for p in root.glob(f"*/*{SUFFIX}")]
    return len(sizes), sum(sizes)
