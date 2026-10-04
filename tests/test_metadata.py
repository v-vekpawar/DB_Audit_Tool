"""Step 3 checkpoint (no server needed): inspector paths run against
SQLite, exclusion rules hold, brief/detail shapes match the design."""

import pytest
from sqlalchemy import create_engine, text

from app.metadata import (
    database_structure,
    effective_schema,
    is_excluded,
    list_tables,
    table_detail,
)


@pytest.fixture()
def sqlite_engine():
    e = create_engine("sqlite://")
    with e.begin() as c:
        c.execute(text("CREATE TABLE parent (id INTEGER PRIMARY KEY, code VARCHAR(8), note VARCHAR(40), UNIQUE(code))"))
        c.execute(text("CREATE TABLE child (id INTEGER PRIMARY KEY, parent_id INT NOT NULL REFERENCES parent(id), val INT)"))
        c.execute(text("CREATE VIEW v_mine AS SELECT id FROM parent"))
    return e


def test_effective_schema():
    assert effective_schema("postgresql", None) == "public"
    assert effective_schema("postgresql", "shop") == "shop"
    assert effective_schema("mysql", None) is None
    assert effective_schema("oracle", "SCOTT") == "SCOTT"


def test_is_excluded():
    assert is_excluded("x", "pg_catalog")
    assert is_excluded("x", "information_schema")
    assert is_excluded("pg_stat_x", "public")
    assert not is_excluded("orders", "public")
    assert not is_excluded("orders", None)


def test_list_tables_brief_shape(sqlite_engine):
    with sqlite_engine.connect() as conn:
        briefs = list_tables(conn, "mysql")
    by_name = {b["name"]: b for b in briefs}
    assert set(by_name) == {"parent", "child"}  # view excluded
    assert by_name["parent"] == {"name": "parent", "columns": 3,
                                 "has_pk": True, "fks": 0}
    assert by_name["child"]["fks"] == 1 and by_name["child"]["has_pk"]


def test_table_detail_keys(sqlite_engine):
    with sqlite_engine.connect() as conn:
        d = table_detail(conn, "mysql", "child")
        parent = table_detail(conn, "mysql", "parent")
    assert d["pk"] == ["id"]
    cols = {c["name"]: c for c in d["columns"]}
    assert cols["parent_id"]["fk"]["table"] == "parent"
    assert cols["parent_id"]["fk"]["column"] == "id"
    assert cols["val"]["fk"] is None
    assert ["code"] in [sorted(u) for u in parent["uniques"]]


def test_database_structure_counts(sqlite_engine):
    with sqlite_engine.connect() as conn:
        briefs = list_tables(conn, "mysql")
        struct = database_structure(conn, "mysql", briefs)
    assert len(struct["tables"]) == 2
