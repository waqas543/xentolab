import duckdb
import pytest

from data_platform.quality.checks import (
    CoordinateCheck,
    DomainCheck,
    DuplicateRateCheck,
    NotNullCheck,
    PhoneCheck,
    RangeCheck,
    RecordCountCheck,
    RegexCheck,
    SchemaCheck,
    UniqueCheck,
)
from data_platform.quality.runner import enforce
from data_platform.errors import QualityGateError


@pytest.fixture
def con():
    con = duckdb.connect()
    con.execute("""
        CREATE TABLE t AS SELECT * FROM (VALUES
            (1, 'Acme', 31.5, 74.3, 'acme.com', '+92', NULL),
            (2, NULL, 95.0, 74.3, 'bad domain', '0300', '+923001234567'),
            (3, 'Acme', 0.0, 0.0, NULL, NULL, NULL),
            (3, '', 24.8, 67.0, 'shop.com.pk', NULL, NULL)
        ) v(id, name, latitude, longitude, normalized_domain, phone, normalized_phone)
    """)
    return con


def test_not_null_counts_null_and_empty(con):
    outcome = NotNullCheck(name="n", column="name").evaluate(con, "t", {})
    assert (outcome.records_checked, outcome.records_failed) == (4, 2)
    assert outcome.failure_percentage == 50


def test_unique(con):
    assert UniqueCheck(name="u", columns=("id",)).evaluate(con, "t", {}).records_failed == 1


def test_range_and_coordinates(con):
    assert RangeCheck(name="r", column="latitude", min_value=-90, max_value=90).evaluate(con, "t", {}).records_failed == 1
    # out-of-range latitude and the (0,0) "null island"
    assert CoordinateCheck(name="c").evaluate(con, "t", {}).records_failed == 2


def test_regex_and_domain(con):
    assert RegexCheck(name="re", column="normalized_domain", pattern=r"[a-z.]+").evaluate(con, "t", {}).records_failed == 1
    assert DomainCheck(name="d").evaluate(con, "t", {}).records_failed == 1


def test_phone_requires_normalization(con):
    assert PhoneCheck(name="p").evaluate(con, "t", {}).records_failed == 1


def test_record_count_against_previous(con):
    ok = RecordCountCheck(name="rc", min_rows=1, max_change_pct=50).evaluate(con, "t", {"previous_row_count": 3})
    assert ok.records_failed == 0
    shrunk = RecordCountCheck(name="rc", max_change_pct=50).evaluate(con, "t", {"previous_row_count": 100})
    assert shrunk.records_failed == 1 and "changed" in shrunk.details["problems"][0]


def test_record_count_ignores_change_when_sources_changed(con):
    jump = RecordCountCheck(name="rc", max_change_pct=25).evaluate(
        con, "t", {"previous_row_count": 1, "composition_changed": True, "sources": ["osm", "overture"],
                   "previous_sources": ["osm"]})
    assert jump.records_failed == 0 and "inputs changed" in jump.details["note"]


def test_schema_allows_extra_columns_but_requires_types(con):
    good = SchemaCheck(name="s", required=(("latitude", "DECIMAL"), ("name", "VARCHAR"))).evaluate(con, "t", {})
    assert good.records_failed == 0
    bad = SchemaCheck(name="s", required=(("missing", "VARCHAR"), ("name", "INTEGER"))).evaluate(con, "t", {})
    assert bad.records_failed == 2


def test_duplicate_rate(con):
    outcome = DuplicateRateCheck(name="dup", columns=("name",)).evaluate(con, "t", {})
    assert (outcome.records_checked, outcome.records_failed) == (3, 2)


def test_threshold_and_severity_overrides():
    check = NotNullCheck(name="n", column="name", severity="error", threshold_pct=0)
    relaxed = check.with_overrides({"threshold_pct": 60, "severity": "warn", "column": "ignored"})
    assert (relaxed.threshold_pct, relaxed.severity, relaxed.column) == (60, "warn", "name")


def test_enforce_raises_only_for_error_severity():
    base = {"dataset_id": "d", "check_name": "c", "failure_percentage": 10.0}
    assert enforce([{**base, "status": "warned", "severity": "warn"}])["warned"] == 1
    with pytest.raises(QualityGateError):
        enforce([{**base, "status": "failed", "severity": "error"}])
    with pytest.raises(QualityGateError):
        enforce([{**base, "status": "error", "severity": "error"}])
