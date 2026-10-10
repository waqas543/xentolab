"""Artifact and dataset file storage.

Everything that touches files goes through `ArtifactStorage` with opaque, relative
keys ("bronze/osm/artifacts/12/pakistan.osm.pbf"). Only `LocalArtifactStorage` is
implemented (the platform runs fully locally); another backend only needs to
implement this interface.
"""

import hashlib
import os
import shutil
import uuid
from abc import ABC, abstractmethod
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from .errors import InsufficientDiskError, PermanentError


@dataclass(frozen=True)
class StoredObject:
    key: str
    size_bytes: int


def validate_key(key: str) -> str:
    if not key or key.startswith("/") or "\\" in key:
        raise PermanentError(f"Invalid storage key: {key!r}")
    parts = key.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise PermanentError(f"Invalid storage key: {key!r}")
    return key


class ArtifactStorage(ABC):
    @abstractmethod
    def exists(self, key: str) -> bool: ...

    @abstractmethod
    def size(self, key: str) -> int: ...

    @abstractmethod
    def put_file(self, source: Path, key: str, *, link: bool = False) -> StoredObject:
        """Store a local file under `key`. Never overwrites. `link` hard-links when possible."""

    @abstractmethod
    @contextmanager
    def open_write(self, key: str) -> Iterator[BinaryIO]:
        """Write a new object atomically: it only appears under `key` once the block exits cleanly."""

    @abstractmethod
    def open_read(self, key: str) -> BinaryIO: ...

    @abstractmethod
    def local_path(self, key: str) -> Path:
        """A local filesystem path for engines (DuckDB, pyosmium) that need one."""

    @abstractmethod
    def staging_directory(self) -> tuple[str, Path]:
        """A fresh private directory for building a dataset version: (key, path)."""

    @abstractmethod
    def commit_directory(self, staging_key: str, key: str) -> None:
        """Atomically publish a staging directory under `key` (which must not exist)."""

    @abstractmethod
    def delete(self, key: str) -> None: ...

    @abstractmethod
    def list(self, prefix: str) -> list[StoredObject]: ...

    @abstractmethod
    def usage_bytes(self, prefix: str = "") -> int: ...

    @abstractmethod
    def free_bytes(self) -> int: ...

    @abstractmethod
    def uri(self, key: str) -> str: ...

    def ensure_free_space(self, needed_bytes: int, reserve_bytes: int) -> None:
        free = self.free_bytes()
        if free < needed_bytes + reserve_bytes:
            raise InsufficientDiskError(
                f"Not enough free disk: need {(needed_bytes + reserve_bytes) / 1e9:.2f} GB "
                f"(incl. {reserve_bytes / 1e9:.2f} GB reserve), have {free / 1e9:.2f} GB",
                context={"needed_bytes": needed_bytes, "free_bytes": free},
            )


class LocalArtifactStorage(ArtifactStorage):
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / validate_key(key)).resolve()
        if self.root not in path.parents and path != self.root:
            raise PermanentError(f"Key escapes storage root: {key!r}")
        return path

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def size(self, key: str) -> int:
        path = self._path(key)
        if path.is_dir():
            return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
        return path.stat().st_size

    def put_file(self, source: Path, key: str, *, link: bool = False) -> StoredObject:
        target = self._path(key)
        if target.exists():
            raise PermanentError(f"Refusing to overwrite existing object {key!r}")
        target.parent.mkdir(parents=True, exist_ok=True)
        source = Path(source)

        if link:
            try:
                os.link(source, target)
                return StoredObject(key, target.stat().st_size)
            except OSError:
                pass  # different filesystem or links unsupported: fall back to copying

        self.ensure_free_space(source.stat().st_size, 0)
        temp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        try:
            shutil.copyfile(source, temp)
            os.replace(temp, target)
        finally:
            temp.unlink(missing_ok=True)
        return StoredObject(key, target.stat().st_size)

    @contextmanager
    def open_write(self, key: str) -> Iterator[BinaryIO]:
        target = self._path(key)
        if target.exists():
            raise PermanentError(f"Refusing to overwrite existing object {key!r}")
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        try:
            with open(temp, "wb") as handle:
                yield handle
            os.replace(temp, target)
        finally:
            temp.unlink(missing_ok=True)

    def open_read(self, key: str) -> BinaryIO:
        return open(self._path(key), "rb")

    def local_path(self, key: str) -> Path:
        return self._path(key)

    def staging_directory(self) -> tuple[str, Path]:
        key = f"_staging/{uuid.uuid4().hex}"
        path = self._path(key)
        path.mkdir(parents=True)
        return key, path

    def commit_directory(self, staging_key: str, key: str) -> None:
        source = self._path(staging_key)
        target = self._path(key)
        if target.exists():
            raise PermanentError(f"Refusing to overwrite existing directory {key!r}")
        target.parent.mkdir(parents=True, exist_ok=True)
        os.rename(source, target)

    def delete(self, key: str) -> None:
        path = self._path(key)
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)

    def list(self, prefix: str) -> list[StoredObject]:
        base = self._path(prefix) if prefix else self.root
        if not base.exists():
            return []
        files = [base] if base.is_file() else sorted(p for p in base.rglob("*") if p.is_file())
        return [StoredObject(str(p.relative_to(self.root)), p.stat().st_size) for p in files]

    def usage_bytes(self, prefix: str = "") -> int:
        # Hard-linked files are counted once per link here; see docs/data-platform/storage.md.
        return sum(obj.size_bytes for obj in self.list(prefix))

    def free_bytes(self) -> int:
        return shutil.disk_usage(self.root).free

    def uri(self, key: str) -> str:
        return self._path(key).as_uri()


def sha256_file(path: Path, chunk_size: int = 4 * 1024 * 1024, on_progress=None) -> str:
    digest = hashlib.sha256()
    done = 0
    with open(path, "rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
            done += len(chunk)
            if on_progress:
                on_progress(done)
    return digest.hexdigest()


def get_storage() -> ArtifactStorage:
    from .config import get_settings

    return LocalArtifactStorage(get_settings().data_dir)
