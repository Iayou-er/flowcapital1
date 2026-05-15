"""测试 sql_helpers: build_in_clause, row_to_dict"""
import os
import pytest


@pytest.fixture
def sql_helpers():
    os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
    from backend.database.sql_helpers import build_in_clause, row_to_dict
    return build_in_clause, row_to_dict


class TestBuildInClause:
    def test_empty_values(self, sql_helpers):
        build_in_clause, _ = sql_helpers
        sql, params = build_in_clause("ids", [])
        assert sql == "FALSE"
        assert params == {}

    def test_single_value(self, sql_helpers):
        build_in_clause, _ = sql_helpers
        sql, params = build_in_clause("ids", ["abc"])
        assert "IN" in sql
        assert "ids_0" in params
        assert params["ids_0"] == "abc"

    def test_multiple_values(self, sql_helpers):
        build_in_clause, _ = sql_helpers
        sql, params = build_in_clause("ids", ["a", "b", "c"])
        assert "IN" in sql
        assert len(params) == 3

    def test_negate(self, sql_helpers):
        build_in_clause, _ = sql_helpers
        sql, params = build_in_clause("exclude", ["x"], negate=True)
        assert "NOT" in sql


class TestRowToDict:
    def test_basic(self, sql_helpers):
        _, row_to_dict = sql_helpers
        cols = ["id", "name"]
        result = row_to_dict((1, "test"), cols)
        assert result == {"id": 1, "name": "test"}

    def test_tags_split(self, sql_helpers):
        _, row_to_dict = sql_helpers
        cols = ["id", "tags"]
        result = row_to_dict((1, "a,b,c"), cols)
        assert result["tags"] == ["a", "b", "c"]

    def test_keywords_split(self, sql_helpers):
        _, row_to_dict = sql_helpers
        cols = ["id", "keywords"]
        result = row_to_dict((1, "x,y,z"), cols)
        assert result["keywords"] == ["x", "y", "z"]
