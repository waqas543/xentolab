"""Input stage: stream rows from JSONL/CSV, map configurable field names, resolve and normalize company names.

Rows that cannot be used come back as `RejectedRow` (written to skipped.jsonl by the job), never silently lost.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import orjson

from app.core.config import ExtractionRules, JobConfig, NameNormalizationConfig
from app.core.exceptions import InputError
from app.core.models import CompanyInput
from app.core.utils import normalize_whitespace

_PUNCT_EDGES = re.compile(r"^[\s.,;:\-/&]+|[\s.,;:\-/&]+$")
_KEY_NOISE = re.compile(r"[^\w]+")


@dataclass
class RejectedRow:
    row_number: int
    reason: str
    details: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------- reading


def read_rows(path: Path, fmt: str = "auto") -> Iterator[tuple[int, dict[str, Any] | RejectedRow]]:
    """Yield (row_number, row) lazily; malformed lines become RejectedRow instead of aborting the run."""
    if not path.is_file():
        raise InputError(f"Input file not found: {path}")
    if fmt == "auto":
        fmt = "csv" if path.suffix.lower() == ".csv" else "jsonl"
    if fmt == "csv":
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for number, row in enumerate(csv.DictReader(handle), start=1):
                yield number, {k.strip(): v for k, v in row.items() if k is not None}
        return
    with path.open("rb") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = orjson.loads(line)
            except orjson.JSONDecodeError as exc:
                yield number, RejectedRow(number, "invalid_json", str(exc), {"line": line.decode("utf-8", "replace")})
                continue
            if not isinstance(value, dict):
                yield number, RejectedRow(number, "not_an_object", type(value).__name__, {"value": value})
                continue
            yield number, value


def _first(row: dict[str, Any], names: list[str]) -> str | None:
    """Value of the first listed field that is present and non-blank (field names match case-insensitively)."""
    lowered = {key.lower(): key for key in row}
    for name in names:
        key = name if name in row else lowered.get(name.lower())
        if key is None:
            continue
        value = row[key]
        if value is None:
            continue
        text = normalize_whitespace(str(value))
        if text and text.lower() not in ("null", "none", "n/a", "na", "-"):
            return text
    return None


# ---------------------------------------------------------------- names


class NameNormalizer:
    def __init__(self, options: NameNormalizationConfig, rules: ExtractionRules) -> None:
        self.options = options
        self._remove = re.compile(options.remove_chars)
        suffixes = [*rules.company_suffixes, *options.extra_suffixes]
        # Longest first so "(pvt) ltd" wins over "ltd"; anchored at the end, case-insensitive.
        alternatives = "|".join(re.escape(s.strip()).replace(r"\ ", r"\s*")
                                for s in sorted(set(suffixes), key=len, reverse=True) if s.strip())
        # A suffix must follow a separator (or start with "("), so "Unlimited" never loses "limited".
        self._suffix = (re.compile(rf"(?:(?:[\s,.\-]+|(?=\())(?:{alternatives}))+\s*$", re.I)
                        if alternatives else None)

    def clean(self, name: str) -> str:
        """Trim, collapse whitespace and drop characters that are never part of a company name."""
        # Drop symbols before NFKC so e.g. "™" disappears instead of becoming "TM".
        return normalize_whitespace(self._remove.sub(" ", name))

    def search_name(self, name: str) -> str:
        cleaned = self.clean(name)
        if self.options.strip_suffixes and self._suffix:
            stripped = self._suffix.sub("", f" {cleaned}").strip()
            stripped = _PUNCT_EDGES.sub("", stripped)
            if len(stripped) >= 2 and len(stripped.split()) >= self.options.min_words_after_strip:
                cleaned = stripped
        return normalize_whitespace(cleaned.replace("()", " "))

    @staticmethod
    def key(search_name: str) -> str:
        return _KEY_NOISE.sub(" ", search_name.lower()).strip()


class InputReader:
    """Turns raw rows into CompanyInput, rejecting rows without a usable name and duplicate companies."""

    def __init__(self, config: JobConfig, rules: ExtractionRules) -> None:
        if config.input_file is None:
            raise InputError(f"Job {config.job_name!r} has no input_file configured")
        self.config = config
        self.normalizer = NameNormalizer(config.name_normalization, rules)
        self._seen: dict[str, int] = {}

    def resolve(self, row_number: int, row: dict[str, Any]) -> CompanyInput | RejectedRow:
        company_name = _first(row, self.config.company_name_fields)
        business_name = _first(row, self.config.fallback_company_name_fields)
        chosen = company_name or business_name
        if not chosen:
            return RejectedRow(row_number, "missing_company_name",
                               f"none of {self.config.company_name_fields + self.config.fallback_company_name_fields}"
                               " present", row)
        resolved = self.normalizer.clean(chosen)
        search_name = self.normalizer.search_name(chosen)
        key = self.normalizer.key(search_name)
        if len(key.replace(" ", "")) < 2:
            return RejectedRow(row_number, "invalid_company_name", f"{chosen!r} is empty after normalization", row)
        if key in self._seen:
            return RejectedRow(row_number, "duplicate_company", f"same company as row {self._seen[key]}", row)
        self._seen[key] = row_number
        mapped = {name: _first(row, aliases) for name, aliases in self.config.field_map.items()}
        known = {name: mapped.get(name) for name in ("ntn", "registration_no", "province", "city", "address")}
        return CompanyInput(row_number=row_number, company_key=key, company_name=company_name,
                            business_name=business_name, resolved_name=resolved, search_name=search_name,
                            raw=row, **known)

    def __iter__(self) -> Iterator[CompanyInput | RejectedRow]:
        assert self.config.input_file is not None
        for row_number, row in read_rows(self.config.input_file, self.config.input_format):
            yield row if isinstance(row, RejectedRow) else self.resolve(row_number, row)
