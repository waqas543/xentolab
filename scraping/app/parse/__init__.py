"""Parsers. Importing this package registers the built-in parsers."""

from app.parse import (  # noqa: F401
    contact_extractor,
    google_parser,
    html_parser,
    linkedin_parser,
    metadata_extractor,
    search_result_parser,
)
from app.parse.base_parser import PARSERS, BaseParser, ParseContext, create_parsers, register_parser

__all__ = ["PARSERS", "BaseParser", "ParseContext", "create_parsers", "register_parser"]
