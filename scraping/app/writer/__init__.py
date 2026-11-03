"""Writers. Importing this package registers the built-in writers."""

from app.writer import jsonl_writer  # noqa: F401
from app.writer.base_writer import WRITERS, BaseWriter, register_writer
from app.writer.output_manager import OutputManager

__all__ = ["WRITERS", "BaseWriter", "OutputManager", "register_writer"]
