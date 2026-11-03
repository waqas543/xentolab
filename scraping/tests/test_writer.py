"""Generic JSONL writer: 1,000-record rotation, restart continuation, torn-line repair, UTF-8, validation."""

from __future__ import annotations

from pathlib import Path

import orjson
import pytest

from app.core.exceptions import WriterError
from app.core.models import SearchResultRecord
from app.writer import OutputManager
from app.writer.jsonl_writer import JsonlWriter, RotatingJsonlFile


def lines(path: Path) -> list[dict]:
    return [orjson.loads(line) for line in path.read_bytes().splitlines()]


def record(i: int, **overrides) -> SearchResultRecord:
    data = {"record_id": f"id-{i}", "source": "fbr", "company_name": "شرکت Engro", "search_query": "q",
            "job_id": "job-1", "job_name": "fbr_company_enrichment", "loader": "load-page", "dedup_key": f"k{i}",
            "rank": 1, "result_title": "Title", "result_url": f"https://engro.com/{i}", "domain": "engro.com"}
    return SearchResultRecord(**(data | overrides))


def test_rotates_every_1000_records(tmp_path: Path):
    file = RotatingJsonlFile(tmp_path, "fbr_company_search_results", max_records=1000)
    for i in range(2500):
        file.write_line(orjson.dumps({"i": i}) + b"\n")
    file.close()
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ["fbr_company_search_results_0001.jsonl", "fbr_company_search_results_0002.jsonl",
                     "fbr_company_search_results_0003.jsonl"]
    counts = [len(lines(tmp_path / n)) for n in names]
    assert counts == [1000, 1000, 500]
    assert lines(tmp_path / names[1])[0] == {"i": 1000}


def test_restart_continues_the_last_file(tmp_path: Path):
    first = RotatingJsonlFile(tmp_path, "x", max_records=3)
    for i in range(4):
        first.write_line(orjson.dumps({"i": i}) + b"\n")
    first.close()
    second = RotatingJsonlFile(tmp_path, "x", max_records=3)
    for i in range(4, 7):
        second.write_line(orjson.dumps({"i": i}) + b"\n")
    second.close()
    assert [len(lines(tmp_path / f"x_000{n}.jsonl")) for n in (1, 2, 3)] == [3, 3, 1]


def test_torn_last_line_is_repaired_on_open(tmp_path: Path):
    path = tmp_path / "x_0001.jsonl"
    path.write_bytes(b'{"i":0}\n{"i":1}\n{"i":2, "trunc')
    file = RotatingJsonlFile(tmp_path, "x", max_records=10)
    file.write_line(b'{"i":3}\n')
    file.close()
    assert lines(path) == [{"i": 0}, {"i": 1}, {"i": 3}]


def test_rejects_multiline_payloads(tmp_path: Path):
    file = RotatingJsonlFile(tmp_path, "x")
    with pytest.raises(WriterError):
        file.write_line(b'{"a":1}\n{"b":2}\n')


def test_output_manager_routes_validates_and_keeps_unicode(tmp_path: Path):
    writer = JsonlWriter(tmp_path, lambda t: f"fbr_company_{t}", 1000)
    manager = OutputManager([writer])
    manager.write(record(1, raw_snippet="line one\nline two"))
    manager.close()
    path = tmp_path / "fbr_company_search_results_0001.jsonl"
    raw = path.read_bytes()
    assert raw.count(b"\n") == 1  # embedded newline escaped, one record per line
    assert "شرکت".encode() in raw  # ensure_ascii=False equivalent
    [data] = lines(path)
    assert data["result_url"] == "https://engro.com/1"
    assert data["status"] == "success"
    assert data["scraped_at"].endswith("Z") or "+00:00" in data["scraped_at"]
    assert manager.counts == {"search_results": 1}


def test_invalid_records_are_never_written(tmp_path: Path):
    manager = OutputManager([JsonlWriter(tmp_path, lambda t: t, 1000)])
    good = record(1)
    bad = record(2)
    bad.result_url = "not a url"  # mutated after construction
    with pytest.raises(WriterError):
        manager.write_all([good, bad])
    manager.close()
    assert not any(tmp_path.iterdir()) or all(p.stat().st_size == 0 for p in tmp_path.iterdir())
