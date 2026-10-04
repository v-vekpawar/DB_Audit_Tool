"""Step 4 checkpoint (no server needed): exclusions, integer coding,
NULL policy, dialect SQL shapes, sampling on SQLite."""

import pytest
from sqlalchemy import create_engine, text

from app.loader import (
    bounded_count,
    bounded_count_query,
    code_columns,
    exclusion_reason,
    load_columns,
    load_table,
    split_columns,
)


@pytest.fixture()
def sqlite_engine():
    e = create_engine("sqlite://")
    with e.begin() as c:
        c.execute(text("CREATE TABLE t (id INTEGER PRIMARY KEY, grp INT, val VARCHAR(20), body TEXT, n INT)"))
        for i in range(1, 251):
            c.execute(text("INSERT INTO t VALUES (:i, :g, :v, :b, :n)"),
                      {"i": i, "g": (i % 10), "v": "v%d" % (i % 5),
                       "b": "long text %d" % i,
                       "n": None if i % 4 == 0 else i})
    return e


def fake_detail():
    return {"name": "t", "pk": ["id"],
            "columns": [{"name": "id", "type": "INTEGER"},
                        {"name": "grp", "type": "INT"},
                        {"name": "val", "type": "VARCHAR(20)"},
                        {"name": "body", "type": "TEXT"},
                        {"name": "n", "type": "INT"}]}


def test_exclusion_classifier():
    assert exclusion_reason("BLOB") == "binary"
    assert exclusion_reason("BYTEA") == "binary"
    assert exclusion_reason("VARBINARY(16)") == "binary"
    assert exclusion_reason("JSON") == "json"
    assert exclusion_reason("MEDIUMTEXT") == "wide-text"
    assert exclusion_reason("TEXT") == "wide-text"
    assert exclusion_reason("CLOB") == "wide-text"
    assert exclusion_reason("VARCHAR(60)") is None
    assert exclusion_reason("INT") is None
    assert exclusion_reason("DECIMAL(10,2)") is None
    assert exclusion_reason("DATE") is None


def test_split_columns():
    searchable, excluded = split_columns(fake_detail())
    assert searchable == ["id", "grp", "val", "n"]
    assert [e["name"] for e in excluded] == ["body"]


def test_code_columns_null_policy():
    cc = code_columns({"a": ["x", "y", "x", None, None],
                       "b": [1, 1, 1, 1, 1]})
    coded, ratios, labels, counts = cc.coded, cc.null_ratio, cc.labels, cc.counts
    assert coded["a"][:3] == [coded["a"][0], coded["a"][1], coded["a"][0]]
    assert coded["a"][3] is None and coded["a"][4] is None
    assert ratios["a"] == 0.4 and ratios["b"] == 0.0
    assert len(set(coded["b"])) == 1
    assert labels["a"][coded["a"][0]] == "x"
    assert labels["b"] == [1]
    assert counts["a"] == [2, 1] and counts["b"] == [5]


def test_bounded_count_dialect_shapes():
    from sqlalchemy.dialects import mysql, oracle, postgresql
    kw = {"compile_kwargs": {"literal_binds": True}}
    my = str(bounded_count_query("t", "mysql", None, 50000).compile(dialect=mysql.dialect(), **kw))
    pg = str(bounded_count_query("t", "postgresql", "public", 50000).compile(dialect=postgresql.dialect(), **kw))
    ora = str(bounded_count_query("t", "oracle", "SCOTT", 50000).compile(dialect=oracle.dialect(), **kw))
    assert "LIMIT" in my and "50001" in my
    assert "LIMIT" in pg
    assert "FETCH FIRST" in ora


def test_bounded_count_values(sqlite_engine):
    with sqlite_engine.connect() as conn:
        assert bounded_count(conn, "mysql", "t", max_rows=1000) == 250
        assert bounded_count(conn, "mysql", "t", max_rows=100) == 101


def test_load_table_sampling_and_coding(sqlite_engine):
    with sqlite_engine.connect() as conn:
        lt = load_table(conn, "mysql", fake_detail(), known_count=250, max_rows=100)
    assert lt.sampled and lt.row_count == 100
    assert lt.coded["id"][0] != lt.coded["id"][1]  # first-N in PK order
    assert lt.excluded[0]["name"] == "body"
    assert lt.null_ratio["n"] == pytest.approx(0.25)
    assert set(lt.coded["val"]) == set(range(5))
    with sqlite_engine.connect() as conn:
        lt2 = load_table(conn, "mysql", fake_detail(), known_count=250, max_rows=1000)
    assert not lt2.sampled and lt2.row_count == 250


def test_load_columns_subset(sqlite_engine):
    with sqlite_engine.connect() as conn:
        rows = load_columns(conn, "mysql", "t", ["id", "grp"], limit=3)
    assert rows == [(1, 1), (2, 2), (3, 3)]
