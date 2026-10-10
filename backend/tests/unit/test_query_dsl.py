import duckdb
import pytest

from data_platform.errors import ParameterError
from data_platform.query.dsl import DUCKDB, POSTGRES, Group, QueryRequest, compile_filters, compile_search, partition_values
from data_platform.query.registry import get_dataset

GOLD = get_dataset("gold.businesses").column_map
SILVER = get_dataset("silver.places").column_map


def group(*conditions, operator="and"):
    return Group.model_validate({"operator": operator, "conditions": list(conditions)})


def test_spec_example_compiles_to_parameters_only():
    filters = group({"field": "country_code", "operator": "equals", "value": "PK"},
                    {"field": "category", "operator": "equals", "value": "pharmacy"},
                    {"field": "website", "operator": "is_not_null"})
    sql, params = compile_filters(filters, GOLD, POSTGRES)
    assert sql == '"country_code" = %s AND "category" = %s AND "website" IS NOT NULL'
    assert params == ["PK", "pharmacy"]


def test_values_never_reach_sql_text():
    evil = "x'; DROP TABLE gold.businesses; --"
    sql, params = compile_filters(group({"field": "name", "operator": "contains", "value": evil}), GOLD, POSTGRES)
    assert "DROP" not in sql and params == ["%x'; DROP TABLE gold.businesses; --%"]


@pytest.mark.parametrize("bad", [
    {"field": "name; DROP TABLE x", "operator": "equals", "value": 1},       # unknown field
    {"field": "name", "operator": "gt", "value": "a"},                        # operator not valid for text
    {"field": "completeness", "operator": "equals", "value": "lots"},         # wrong value type
    {"field": "category", "operator": "in", "value": []},                     # empty list
    {"field": "completeness", "operator": "between", "value": [1]},           # malformed range
    {"field": "location", "operator": "within_bbox", "value": [1, 2]},        # malformed bbox
])
def test_invalid_conditions_are_rejected(bad):
    with pytest.raises(ParameterError):
        compile_filters(group(bad), GOLD, POSTGRES)


def test_nested_groups_and_like_escaping():
    filters = group({"field": "city", "operator": "equals", "value": "Lahore"},
                    {"operator": "or", "conditions": [
                        {"field": "name", "operator": "starts_with", "value": "50%_off"},
                        {"field": "has_website", "operator": "equals", "value": "false"}]})
    sql, params = compile_filters(filters, GOLD, POSTGRES)
    assert sql == ('"city" = %s AND ("CAST(\\"name\\" AS TEXT)" ILIKE %s ESCAPE \'\\\' OR "has_website" = %s)'
                   .replace('"CAST(\\"name\\" AS TEXT)"', 'CAST("name" AS TEXT)'))
    assert params == ["Lahore", "50\\%\\_off%", False]


def test_depth_and_size_limits():
    nested = {"field": "city", "operator": "equals", "value": "x"}
    for _ in range(5):
        nested = {"operator": "and", "conditions": [nested]}
    with pytest.raises(ParameterError):
        compile_filters(Group.model_validate(nested), GOLD, POSTGRES)
    many = [{"field": "city", "operator": "equals", "value": str(i)} for i in range(51)]
    with pytest.raises(ParameterError):
        compile_filters(group(*many), GOLD, POSTGRES)


def test_list_and_geo_operators_per_dialect():
    pg_sql, pg_params = compile_filters(group({"field": "sources", "operator": "in", "value": ["osm", "overture"]}),
                                        GOLD, POSTGRES)
    assert pg_sql == '"sources" && ARRAY[%s, %s]::text[]' and pg_params == ["osm", "overture"]
    duck_sql, _ = compile_filters(group({"field": "sources", "operator": "in", "value": ["osm"]}), GOLD, DUCKDB)
    assert duck_sql == 'list_has_any("sources", [?])'
    radius, params = compile_filters(group({"field": "location", "operator": "within_radius",
                                            "value": {"lat": 31.5, "lon": 74.3, "km": 2}}), GOLD, POSTGRES)
    assert "asin" in radius and params == [31.5, 31.5, 74.3, 2.0]


def test_duckdb_execution_matches_semantics():
    con = duckdb.connect()
    con.execute("""CREATE TABLE t AS SELECT * FROM (VALUES
        ('a', 'Lahore', 31.52, 74.35, 75, ['osm']),
        ('b', 'Karachi', 24.86, 67.01, 25, ['osm', 'overture']),
        ('c', NULL, 31.50, 74.30, 0, ['overture'])) v(silver_id, city, latitude, longitude, completeness, sources)""")
    columns = {**SILVER, "completeness": GOLD["completeness"], "sources": GOLD["sources"]}

    def ids(filters):
        sql, params = compile_filters(group(*filters), columns, DUCKDB)
        return [r[0] for r in con.execute(f"SELECT silver_id FROM t WHERE {sql} ORDER BY 1", params).fetchall()]

    assert ids([{"field": "city", "operator": "not_equals", "value": "Lahore"}]) == ["b", "c"]   # NULL included
    assert ids([{"field": "completeness", "operator": "between", "value": [20, 80]}]) == ["a", "b"]
    assert ids([{"field": "sources", "operator": "contains", "value": "overture"}]) == ["b", "c"]
    assert ids([{"field": "location", "operator": "within_radius",
                 "value": {"lat": 31.51, "lon": 74.32, "km": 5}}]) == ["a", "c"]
    assert ids([{"field": "location", "operator": "within_bbox", "value": [67, 24, 68, 25]}]) == ["b"]


def test_search_and_partition_pruning():
    sql, params = compile_search("bank", GOLD, POSTGRES)
    assert sql.count("ILIKE") == len(params) and params[0] == "%bank%"
    filters = group({"field": "source", "operator": "in", "value": ["osm"]})
    assert partition_values(filters, "source") == ["osm"]
    assert partition_values(group({"field": "source", "operator": "equals", "value": "osm"}, operator="or"), "source") is None


def test_request_limits():
    with pytest.raises(ValueError):
        QueryRequest(page_size=10_000)
