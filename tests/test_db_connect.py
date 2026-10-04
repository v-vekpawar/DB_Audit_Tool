"""Step 2 checkpoint (no database needed): URL building, statement guard,
driver-exception mapping, read-only/version SQL constants."""

import pytest

from app.db_connect import (
    ConnectParams,
    assert_select,
    build_url,
    map_exception,
    normalize_db_type,
)


def P(**kw):
    base = dict(db_type="mysql", host="10.0.0.5", port=3306,
                username="u", password="p", database="shop")
    base.update(kw)
    return ConnectParams(**base)


def test_normalize():
    assert normalize_db_type("postgres") == "postgresql"
    assert normalize_db_type("MySQL") == "mysql"
    with pytest.raises(ValueError):
        normalize_db_type("sqlite")


def test_mysql_url_escapes_password():
    url = build_url(P(password="p@ss:word/db?x"))
    assert url.startswith("mysql+pymysql://")
    assert "p@ss" not in url and "%40" in url


def test_postgres_url():
    url = build_url(P(db_type="postgresql", port=5432, database="shop"))
    assert url.startswith("postgresql+pg8000://")
    assert ":5432/shop" in url


def test_oracle_url_needs_service():
    url = build_url(ConnectParams(db_type="oracle", host="h", port=1521,
                                  username="u", password="p", service_name="FREEPDB1"))
    assert url.startswith("oracle+oracledb://") and "service_name=FREEPDB1" in url
    with pytest.raises(ValueError):
        build_url(ConnectParams(db_type="oracle", host="h", port=1521,
                                username="u", password="p"))


def test_guard_allows_reads():
    for sql in ("SELECT 1", "  select a from t",
                "WITH x AS (SELECT 1) SELECT * FROM x",
                "SHOW TABLES", "DESCRIBE t", "EXPLAIN SELECT 1"):
        assert_select(sql)


def test_guard_rejects_writes():
    for sql in ("INSERT INTO t VALUES (1)", "UPDATE t SET a=1",
                "DELETE FROM t", "DROP TABLE t", "CREATE TABLE t (a INT)",
                "ALTER TABLE t ADD COLUMN b INT", "TRUNCATE TABLE t",
                "insert into t values (1)", "/* c */ DELETE FROM t",
                "SELECT 1; DROP TABLE t", ""):
        with pytest.raises(ValueError):
            assert_select(sql)


def fake_mysql(errno, msg="boom"):
    import pymysql.err
    return pymysql.err.OperationalError(errno, msg)


def test_map_mysql_codes():
    assert map_exception(fake_mysql(1045), step="connect")["error"]["code"] == "DB_AUTH_FAILED"
    assert map_exception(fake_mysql(1049), step="connect")["error"]["code"] == "DB_NOT_FOUND"
    assert map_exception(fake_mysql(2003), step="connect")["error"]["code"] == "DB_UNREACHABLE"
    assert map_exception(fake_mysql(2013), step="connect")["error"]["code"] == "DB_TIMEOUT"
    assert map_exception(fake_mysql(2026), step="connect")["error"]["code"] == "DB_SSL_FAILED"


def test_map_pg_states_and_messages():
    e = Exception("password authentication failed for user")
    assert map_exception(e)["error"]["code"] == "DB_AUTH_FAILED"
    e = Exception('database "nosuch" does not exist')
    assert map_exception(e)["error"]["code"] == "DB_NOT_FOUND"
    e = Exception("connection refused: pg_hba?")
    assert map_exception(e)["error"]["code"] == "DB_UNREACHABLE"
    e = Exception("connection timed out after 30s")
    assert map_exception(e)["error"]["code"] == "DB_TIMEOUT"
    e = Exception({"S": "FATAL", "C": "28P01", "M": "nope"})
    assert map_exception(e)["error"]["code"] == "DB_AUTH_FAILED"
    assert map_exception(ImportError("nope"))["error"]["code"] == "DB_DRIVER_MISSING"
    import socket
    assert map_exception(socket.timeout())["error"]["code"] == "DB_TIMEOUT"
    assert map_exception(ConnectionRefusedError())["error"]["code"] == "DB_UNREACHABLE"


def test_error_object_shape_and_message():
    err = map_exception(fake_mysql(2003), step="connect", scan_id="s1")["error"]
    assert err["code"] == "DB_UNREACHABLE"
    assert "Database connection not established" in err["message"]
    assert err["hint"] and err["step"] == "connect" and err["scan_id"] == "s1"
