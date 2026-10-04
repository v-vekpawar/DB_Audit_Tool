"""Database connections: URL building, SSL, read-only, version, guard.

Supports MySQL and PostgreSQL now; Oracle URL building is included as a
pure function (untested against a server until build step 13).
"""

import argparse
import getpass
import logging
import re
import socket
import ssl
from dataclasses import dataclass
from urllib.parse import quote_plus

from . import config
from .errors import make_error
from .logging_setup import StepTimer, get_logger, set_secrets

READONLY_SQL = {
    "mysql": "SET SESSION TRANSACTION READ ONLY",
    "postgresql": "SET default_transaction_read_only = on",
    "oracle": "SET TRANSACTION READ ONLY",
}

READONLY_CHECK_SQL = {
    "mysql": "SELECT @@transaction_read_only",
    "postgresql": "SHOW transaction_read_only",
    "oracle": None,  # verified in step 13 against a live server
}

VERSION_SQL = {
    "mysql": "SELECT VERSION()",
    "postgresql": "SELECT version()",
    "oracle": "SELECT banner FROM v$version WHERE ROWNUM = 1",
}

ENGINE_DISPLAY = {"mysql": "MySQL", "postgresql": "PostgreSQL", "oracle": "Oracle"}


@dataclass
class ConnectParams:
    db_type: str  # mysql | postgresql | oracle
    host: str
    port: int
    username: str
    password: str
    database: str = ""  # Oracle: unused (service_name + schema instead)
    schema: str | None = None  # postgresql schema / oracle owner
    service_name: str | None = None  # oracle only
    ssl: bool = False


def normalize_db_type(db_type: str) -> str:
    t = (db_type or "").strip().lower()
    if t in ("mysql", "postgresql", "postgres", "oracle"):
        return "postgresql" if t == "postgres" else t
    raise ValueError("unsupported db_type: %r" % (db_type,))


def default_port(db_type: str) -> int:
    return config.DEFAULT_PORTS[normalize_db_type(db_type)]


def build_url(p: ConnectParams) -> str:
    """SQLAlchemy URL for the engine. Password is URL-escaped."""
    t = normalize_db_type(p.db_type)
    user = quote_plus(p.username or "")
    pw = quote_plus(p.password or "")
    auth = "%s:%s@" % (user, pw) if (p.username or p.password) else ""
    host = p.host or "localhost"
    port = p.port or default_port(t)
    if t == "mysql":
        if not p.database:
            raise ValueError("database is required for MySQL")
        return "mysql+pymysql://%s%s:%d/%s" % (auth, host, port, quote_plus(p.database))
    if t == "postgresql":
        if not p.database:
            raise ValueError("database is required for PostgreSQL")
        return "postgresql+pg8000://%s%s:%d/%s" % (auth, host, port, quote_plus(p.database))
    if not p.service_name:
        raise ValueError("service_name is required for Oracle")
    return "oracle+oracledb://%s%s:%d/?service_name=%s" % (
        auth, host, port, quote_plus(p.service_name))


def _unverified_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def connect_args_for(p: ConnectParams) -> dict:
    """Per-driver connect kwargs: timeouts everywhere, SSL when asked."""
    t = normalize_db_type(p.db_type)
    q = config.QUERY_TIMEOUT_SECONDS
    if t == "mysql":
        args: dict = {"connect_timeout": q, "read_timeout": q, "write_timeout": q}
        if p.ssl:
            args["ssl"] = _unverified_context()
        return args
    if t == "postgresql":
        args = {"timeout": q}
        if p.ssl:
            args["ssl_context"] = _unverified_context()
        return args
    return {}


def create_engine(p: ConnectParams, url: str):
    try:
        from sqlalchemy import create_engine as _create
        from sqlalchemy.pool import NullPool
    except ImportError as e:
        raise _coded("DB_DRIVER_MISSING", "connect", "-", p) from e
    try:
        return _create(url, connect_args=connect_args_for(p), poolclass=NullPool)
    except ImportError as e:
        raise _coded("DB_DRIVER_MISSING", "connect", "-", p) from e


class ConnectError(Exception):
    def __init__(self, error_obj: dict):
        super().__init__(error_obj["error"]["code"])
        self.error_obj = error_obj


def _coded(code: str, step: str, scan_id: str, p: ConnectParams | None = None) -> ConnectError:
    return ConnectError(make_error(code, step=step, scan_id=scan_id))


# ---------------------------------------------------------------------
# Statement guard: only SELECT-family statements may go through the
# query path. Connection-setup statements are constants in this module.
# ---------------------------------------------------------------------

_ALLOWED_PREFIXES = ("SELECT", "WITH", "SHOW", "DESCRIBE", "DESC", "EXPLAIN")
_BLOCKED_PREFIXES = ("INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "ALTER",
                     "TRUNCATE", "REPLACE", "GRANT", "REVOKE", "CALL", "EXEC",
                     "EXECUTE", "HANDLER", "LOAD", "RENAME", "SET")


def _strip_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    sql = re.sub(r"--[^\n]*", " ", sql)
    return sql.strip()


def assert_select(sql: str) -> None:
    """Raise ValueError unless sql is exactly one read-only statement."""
    body = _strip_comments(sql)
    if not body:
        raise ValueError("empty statement")
    parts = [p.strip() for p in body.split(";") if p.strip()]
    if len(parts) > 1:
        raise ValueError("multiple statements are not allowed")
    first = re.match(r"[A-Za-z]+", parts[0])
    word = first.group(0).upper() if first else ""
    if word in _BLOCKED_PREFIXES or word not in _ALLOWED_PREFIXES:
        raise ValueError("only SELECT-family statements are allowed, got: %s" % word)


# ---------------------------------------------------------------------
# Driver exception -> error code mapping
# ---------------------------------------------------------------------

def _pg_sqlstate(exc: BaseException) -> str | None:
    for a in getattr(exc, "args", ()):
        if isinstance(a, dict) and a.get("C"):
            return str(a["C"])
    m = re.search(r"\b(28P01|3D000|28000|08001|08004|08006)\b", str(exc))
    return m.group(1) if m else None


def map_exception(exc: BaseException, step: str = "connect",
                  scan_id: str = "-") -> dict:
    """Map any connection/query failure to the structured error object."""
    if isinstance(exc, ConnectError):
        return exc.error_obj
    if isinstance(exc, (ImportError, ModuleNotFoundError)):
        return make_error("DB_DRIVER_MISSING", step=step, scan_id=scan_id)
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return make_error("DB_TIMEOUT", step=step, scan_id=scan_id)
    if isinstance(exc, ssl.SSLError):
        return make_error("DB_SSL_FAILED", step=step, scan_id=scan_id)

    orig = getattr(exc, "orig", None) or getattr(exc, "__cause__", None)
    if orig is not None and orig is not exc:
        return map_exception(orig, step=step, scan_id=scan_id)

    errno = None
    if getattr(exc, "args", None):
        try:
            errno = int(exc.args[0])
        except (TypeError, ValueError, IndexError):
            errno = None
    if errno == 1045:
        return make_error("DB_AUTH_FAILED", step=step, scan_id=scan_id)
    if errno == 1049:
        return make_error("DB_NOT_FOUND", step=step, scan_id=scan_id)
    if errno in (2002, 2003, 2005):
        return make_error("DB_UNREACHABLE", step=step, scan_id=scan_id)
    if errno in (2013, 2006):
        return make_error("DB_TIMEOUT", step=step, scan_id=scan_id)
    if errno == 2026:
        return make_error("DB_SSL_FAILED", step=step, scan_id=scan_id)
    if isinstance(exc, ConnectionRefusedError):
        return make_error("DB_UNREACHABLE", step=step, scan_id=scan_id)

    state = _pg_sqlstate(exc)
    if state in ("28P01", "28000"):
        return make_error("DB_AUTH_FAILED", step=step, scan_id=scan_id)
    if state == "3D000":
        return make_error("DB_NOT_FOUND", step=step, scan_id=scan_id)
    if state and state.startswith("08"):
        return make_error("DB_UNREACHABLE", step=step, scan_id=scan_id)

    msg = str(exc).lower()
    if "password authentication failed" in msg or "access denied" in msg or "1045" in msg:
        return make_error("DB_AUTH_FAILED", step=step, scan_id=scan_id)
    if "does not exist" in msg or "unknown database" in msg or "1049" in msg:
        return make_error("DB_NOT_FOUND", step=step, scan_id=scan_id)
    if "ssl" in msg and ("could not" in msg or "failed" in msg or "error" in msg):
        return make_error("DB_SSL_FAILED", step=step, scan_id=scan_id)
    if "timed out" in msg or "timeout" in msg:
        return make_error("DB_TIMEOUT", step=step, scan_id=scan_id)
    if "refused" in msg or "unreachable" in msg or "no connection could be made" in msg \
            or "2003" in msg or "2002" in msg or "could not connect" in msg \
            or "name or service not known" in msg or "nodename nor servname" in msg:
        return make_error("DB_UNREACHABLE", step=step, scan_id=scan_id)
    return make_error("INTERNAL_ERROR", step=step, scan_id=scan_id)


# ---------------------------------------------------------------------
# Connect flow
# ---------------------------------------------------------------------

def connect(p: ConnectParams, scan_id: str = "-"):
    """Open a read-only connection. Returns (engine, connection, version)."""
    from sqlalchemy import text as sa_text
    set_secrets([p.password] if p.password else [])
    t = normalize_db_type(p.db_type)
    url = build_url(p)
    log = get_logger()
    with StepTimer("connect", scan_id):
        log.info("%s %s:%d db=%s ssl=%s", ENGINE_DISPLAY[t], p.host,
                 p.port or default_port(t), p.database or p.service_name,
                 "on" if p.ssl else "off",
                 extra={"scan_id": scan_id, "step": "connect", "table": "-"})
        try:
            engine = create_engine(p, url)
            conn = engine.connect()
        except Exception as e:
            log.error("connect failed: %s: %s", type(e).__name__, e,
                      extra={"scan_id": scan_id, "step": "connect", "table": "-"})
            raise ConnectError(map_exception(e, step="connect", scan_id=scan_id)) from e
        with StepTimer("readonly", scan_id):
            try:
                conn.execute(sa_text(READONLY_SQL[t]))
                # End the implicit transaction: the check below must run in a
                # fresh transaction so session defaults apply (PostgreSQL
                # reports the current transaction's state).
                conn.commit()
                check_sql = READONLY_CHECK_SQL[t]
                if check_sql is not None:
                    val = conn.execute(sa_text(check_sql)).scalar()
                    if str(val).lower() not in ("1", "on", "true", "t"):
                        raise RuntimeError("read-only not confirmed: %r" % (val,))
            except Exception as e:
                log.error("readonly failed: %s: %s", type(e).__name__, e,
                          extra={"scan_id": scan_id, "step": "readonly", "table": "-"})
                try:
                    conn.close()
                except Exception:
                    pass
                if isinstance(e, ConnectError):
                    raise
                raise ConnectError(make_error("DB_READONLY_FAILED", step="readonly",
                                              scan_id=scan_id)) from e
        with StepTimer("version", scan_id):
            try:
                version = str(conn.execute(sa_text(VERSION_SQL[t])).scalar())
            except Exception as e:
                log.error("version failed: %s: %s", type(e).__name__, e,
                          extra={"scan_id": scan_id, "step": "version", "table": "-"})
                try:
                    conn.close()
                except Exception:
                    pass
                raise ConnectError(map_exception(e, step="version", scan_id=scan_id)) from e
        log.info("version=%s", version.split("\n")[0][:80],
                 extra={"scan_id": scan_id, "step": "version", "table": "-"})
        return engine, conn, version


def close(engine, conn) -> None:
    try:
        conn.close()
    finally:
        try:
            engine.dispose()
        except Exception:
            pass


# ---------------------------------------------------------------------
# Smoke CLI: python -m app.db_connect --db-type mysql --host ... 
# ---------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Connect smoke test: prints server version.")
    ap.add_argument("--db-type", required=True)
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--username", default="root")
    ap.add_argument("--password", default=None)
    ap.add_argument("--database", default="normtest")
    ap.add_argument("--schema", default=None)
    ap.add_argument("--service-name", default=None)
    ap.add_argument("--ssl", action="store_true")
    a = ap.parse_args(argv)
    pw = a.password if a.password is not None else getpass.getpass("Password: ")
    from .logging_setup import setup_logging
    setup_logging()
    p = ConnectParams(db_type=a.db_type, host=a.host, port=a.port,
                      username=a.username, password=pw, database=a.database,
                      schema=a.schema, service_name=a.service_name, ssl=a.ssl)
    try:
        engine, conn, version = connect(p, scan_id="smoke")
    except ConnectError as e:
        import json
        print(json.dumps(e.error_obj, indent=2))
        return 2
    print(version)
    close(engine, conn)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
