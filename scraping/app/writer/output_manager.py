"""OutputManager: validates records and fans them out to the job's writers (e.g. jsonl_writer).

Validation = a full round trip through the record's pydantic model, so a record mutated into an invalid state
after construction is rejected instead of written.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.core.config import JobConfig
from app.core.exceptions import WriterError
from app.core.models import BaseRecord
from app.writer.base_writer import BaseWriter, writer_class


class OutputManager:
    def __init__(self, writers: list[BaseWriter]) -> None:
        if not writers:
            raise WriterError("at least one writer is required")
        self.writers = writers
        self.counts: Counter[str] = Counter()

    @classmethod
    def from_config(cls, config: JobConfig) -> OutputManager:
        writers = [writer_class(name)(config.output_dir, config.output_file_prefix, config.max_records_per_jsonl_file,
                                      subdir_for=config.output_subdir)
                   for name in config.writers]
        return cls(writers)

    @staticmethod
    def validate(record: BaseRecord) -> dict[str, Any]:
        data = record.model_dump(mode="json")
        try:
            type(record).model_validate(data)
        except ValidationError as exc:
            raise WriterError(f"invalid {record.record_type} record {record.record_id}: {exc}") from exc
        return data

    def write(self, record: BaseRecord) -> None:
        data = self.validate(record)
        for writer in self.writers:
            writer.write(record.record_type, data)
        self.counts[record.record_type] += 1

    def write_all(self, records: list[BaseRecord]) -> None:
        # Validate everything first so a bad record never leaves a half-written batch behind.
        validated = [(record.record_type, self.validate(record)) for record in records]
        for record_type, data in validated:
            for writer in self.writers:
                writer.write(record_type, data)
            self.counts[record_type] += 1

    def flush(self) -> None:
        for writer in self.writers:
            writer.flush()

    def close(self) -> None:
        for writer in self.writers:
            writer.close()

    def files(self) -> list[Path]:
        return [path for writer in self.writers for path in writer.files()]
