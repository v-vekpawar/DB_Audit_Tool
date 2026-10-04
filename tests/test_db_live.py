"""Step 2 live checkpoint: real connections to both local test DBs.

Skipped unless NORMTEST_LIVE=1. Credentials come from the environment:

  NORMTEST_MYSQL_HOST/PORT/USER/PW/DB (defaults: localhost/3306/root/""/normtest)
  NORMTEST_PG_HOST/PORT/USER/PW/DB     (defaults: localhost/5432/postgres/""/normtest)

PowerShell:
  $env:NORMTEST_LIVE="1"; $env:NORMTEST_MYSQL_PW="secret"; $env:NORMTEST_PG_PW="secret"
  python -m pytest tests/test_db_live.py -q
"""

import os

import pytest

from app.db_connect import ConnectError, ConnectParams, close, connect

LIVE = os.environ.get("NORMTEST_LIVE", "").strip() == "1"
needs_live = pytest.mark.skipif(not LIVE, reason="needs live test DBs (NORMTEST_LIVE=1)")


def env(prefix, name, default):
    return os.environ.get(prefix + "_" + name, default)


def mysql_params(**kw):
    d = dict(db_type="mysql", host=env("NORMTEST_MYSQL", "HOST", "localhost"),
             port=int(env("NORMTEST_MYSQL", "PORT", "3306")),
             username=env("NORMTEST_MYSQL", "USER", "root"),
             password=env("NORMTEST_MYSQL", "PW", ""),
             database=env("NORMTEST_MYSQL", "DB", "normtest"))
    d.update(kw)
    return ConnectParams(**d)


def pg_params(**kw):
    d = dict(db_type="postgresql", host=env("NORMTEST_PG", "HOST", "localhost"),
             port=int(env("NORMTEST_PG", "PORT", "5432")),
             username=env("NORMTEST_PG", "USER", "postgres"),
             password=env("NORMTEST_PG", "PW", ""),
             database=env("NORMTEST_PG", "DB", "normtest"))
    d.update(kw)
    return ConnectParams(**d)


@needs_live
def test_mysql_version():
    engine, conn, version = connect(mysql_params(), scan_id="live")
    assert "8.0" in version
    print("\nmysql version: " + version.strip())
    close(engine, conn)


@needs_live
def test_postgres_version():
    engine, conn, version = connect(pg_params(), scan_id="live")
    assert "PostgreSQL" in version
    print("\npostgres version: " + version.strip())
    close(engine, conn)


@needs_live
def test_mysql_readonly_enforced():
    from sqlalchemy import text
    engine, conn, _ = connect(mysql_params(), scan_id="live")
    assert str(conn.execute(text("SELECT @@transaction_read_only")).scalar()) == "1"
    close(engine, conn)


@needs_live
def test_postgres_readonly_enforced():
    from sqlalchemy import text
    engine, conn, _ = connect(pg_params(), scan_id="live")
    assert conn.execute(text("SHOW transaction_read_only")).scalar() == "on"
    close(engine, conn)


@needs_live
def test_wrong_password_is_auth_failed():
    with pytest.raises(ConnectError) as e:
        connect(mysql_params(password="definitely-wrong-pw"), scan_id="live")
    assert e.value.error_obj["error"]["code"] == "DB_AUTH_FAILED"
    with pytest.raises(ConnectError) as e:
        connect(pg_params(password="definitely-wrong-pw"), scan_id="live")
    assert e.value.error_obj["error"]["code"] == "DB_AUTH_FAILED"


@needs_live
def test_wrong_database_is_not_found():
    with pytest.raises(ConnectError) as e:
        connect(mysql_params(database="nosuchdb_xyz"), scan_id="live")
    assert e.value.error_obj["error"]["code"] == "DB_NOT_FOUND"
    with pytest.raises(ConnectError) as e:
        connect(pg_params(database="nosuchdb_xyz"), scan_id="live")
    assert e.value.error_obj["error"]["code"] == "DB_NOT_FOUND"


@needs_live
def test_closed_port_is_unreachable():
    with pytest.raises(ConnectError) as e:
        connect(mysql_params(port=9), scan_id="live")
    assert e.value.error_obj["error"]["code"] in ("DB_UNREACHABLE", "DB_TIMEOUT")
    with pytest.raises(ConnectError) as e:
        connect(pg_params(port=9), scan_id="live")
    assert e.value.error_obj["error"]["code"] in ("DB_UNREACHABLE", "DB_TIMEOUT")


@needs_live
def test_metadata_table_list_matches_planted():
    import json
    from pathlib import Path

    from app.metadata import database_structure, list_tables, table_detail

    expected = set(json.load(
        open(Path(__file__).resolve().parent.parent
             / "scripts" / "testdb" / "expected_verdicts.json"))["tables"])
    for params in (mysql_params(), pg_params()):
        engine, conn, _ = connect(params, scan_id="live")
        try:
            briefs = list_tables(conn, params.db_type, scan_id="live")
            assert {b["name"] for b in briefs} == expected
            by_name = {b["name"]: b for b in briefs}
            assert by_name["t_3nf_transitive"]["columns"] == 12
            assert by_name["t_3nf_transitive"]["has_pk"]
            assert by_name["t_3nf_transitive"]["fks"] == 0
            assert by_name["t_cross_copy"]["fks"] == 1
            assert by_name["cc_order_lines"]["fks"] == 2
            det = table_detail(conn, params.db_type, "t_cross_copy",
                               scan_id="live")
            assert det["pk"] == ["order_id"]
            assert det["fks"][0]["referred_table"] == "customers_ref"
            struct = database_structure(conn, params.db_type, briefs,
                                        scan_id="live")
            assert len(struct["tables"]) == len(expected)
        finally:
            close(engine, conn)
