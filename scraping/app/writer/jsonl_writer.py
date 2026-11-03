"""Rotating JSONL output: `<prefix>_0001.jsonl`, `<prefix>_0002.jsonl`, ... with at most N records per file.

Crash safety: each record is one `write()` of a complete line. On open, a torn last line (no trailing newline,
from a crash mid-write) is truncated away, and numbering/counts continue from the existing files, so restarts
append instead of overwriting. Output is UTF-8 with non-ASCII characters kept as-is.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, BinaryIO

import orjson

from app.core.exceptions import WriterError
from app.writer.base_writer import BaseWriter, register_writer

_CHUNK = 1 << 20


class RotatingJsonlFile:
    def __init__(self, directory: Path, prefix: str, max_records: int = 1000) -> None:
        if max_records < 1:
            raise ValueError("max_records must be >= 1")
        self.directory = directory
        self.prefix = prefix
        self.max_records = max_records
        self.part = 0
        self.count = 0
        self.total_written = 0
        self._handle: BinaryIO | None = None
        self.touched: list[Path] = []
        directory.mkdir(parents=True, exist_ok=True)

    def path_for(self, part: int) -> Path:
        return self.directory / f"{self.prefix}_{part:04d}.jsonl"

    def existing_parts(self) -> list[int]:
        pattern = re.compile(rf"^{re.escape(self.prefix)}_(\d{{4,}})\.jsonl$")
        return sorted(int(m.group(1)) for p in self.directory.iterdir() if (m := pattern.match(p.name)))

    def _open(self) -> None:
        parts = self.existing_parts()
        self.part = parts[-1] if parts else 1
        path = self.path_for(self.part)
        self.count = _repair_and_count(path) if path.exists() else 0
        if self.count >= self.max_records:
            self.part += 1
            self.count = 0
        self._open_part()

    def _open_part(self) -> None:
        path = self.path_for(self.part)
        self._handle = path.open("ab")
        if path not in self.touched:
            self.touched.append(path)

    def write_line(self, line: bytes) -> None:
        if not line.endswith(b"\n") or b"\n" in line[:-1]:
            raise WriterError("a JSONL record must be exactly one line ending in a newline")
        if self._handle is None:
            self._open()
        elif self.count >= self.max_records:
            self._rotate()
        assert self._handle is not None
        self._handle.write(line)
        self.count += 1
        self.total_written += 1

    def _rotate(self) -> None:
        self._close_handle()
        self.part += 1
        self.count = 0
        self._open_part()

    def flush(self) -> None:
        if self._handle is not None:
            self._handle.flush()
            os.fsync(self._handle.fileno())

    def _close_handle(self) -> None:
        if self._handle is not None:
            self.flush()
            self._handle.close()
            self._handle = None

    def close(self) -> None:
        self._close_handle()


def _repair_and_count(path: Path) -> int:
    """Drop a torn trailing line (crash mid-write) and return the number of complete lines."""
    with path.open("rb+") as handle:
        size = handle.seek(0, os.SEEK_END)
        if size:
            handle.seek(size - 1)
            if handle.read(1) != b"\n":
                end = size
                while end > 0:
                    start = max(0, end - _CHUNK)
                    handle.seek(start)
                    chunk = handle.read(end - start)
                    newline = chunk.rfind(b"\n")
                    if newline != -1:
                        handle.truncate(start + newline + 1)
                        break
                    end = start
                else:
                    handle.truncate(0)
        handle.seek(0)
        count = 0
        while chunk := handle.read(_CHUNK):
            count += chunk.count(b"\n")
    return count


def dumps_line(data: dict[str, Any]) -> bytes:
    """One JSON object + newline. orjson emits UTF-8 (no \\u escapes for non-ASCII) and escapes newlines."""
    return orjson.dumps(data, option=orjson.OPT_APPEND_NEWLINE)


@register_writer
class JsonlWriter(BaseWriter):
    name = "jsonl_writer"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._files: dict[str, RotatingJsonlFile] = {}

    def _file(self, record_type: str) -> RotatingJsonlFile:
        if record_type not in self._files:
            self._files[record_type] = RotatingJsonlFile(self.directory_for(record_type),
                                                         self.prefix_for(record_type), self.max_records_per_file)
        return self._files[record_type]

    def write(self, record_type: str, data: dict[str, Any]) -> None:
        self._file(record_type).write_line(dumps_line(data))

    def flush(self) -> None:
        for file in self._files.values():
            file.flush()

    def close(self) -> None:
        for file in self._files.values():
            file.close()

    def files(self) -> list[Path]:
        return [path for file in self._files.values() for path in file.touched]
