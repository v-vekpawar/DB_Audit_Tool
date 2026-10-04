"""Schema metadata via sqlalchemy.inspect().

Connect step uses list_tables() (name, column count, has-PK, FK count)
and reads no rows. Full structure comes from table_detail().
Views and system schemas are excluded.
"""

import argparse
import getpass
import json

from .db_connect import ConnectError, ConnectParams, close, connect, map_exception
from .logging_setup import StepTimer, get_logger, setup_logging

EXCLUDED_SCHEMAS = frozenset({
    "information_schema", "pg_catalog", "pg_toast",
    "mysql", "performance_schema", "sys",
})

EXCLUDED_DATABASES = frozenset({
    "information_schema", "mysql", "performance_schema", "sys",
    "template0", "template1",
})

EXCLUDED_TABLE_PREFIXES = ("pg_", "sql_")


def is_excluded(table_name: str, schema: str | None) -> bool:
    if schema is not None and schema.lower() in EXCLUDED_SCHEMAS:
        return True
    low = (table_name or "").lower()
    return low.startswith(EXCLUDED_TABLE_PREFIXES)


def effective_schema(db_type: str, schema: str | None) -> str | None:
    t = (db_type or "").lower()
    if t == "postgresql":
        return schema or "public"
    if t == "oracle":
        # Oracle owners are conventionally uppercase; the inspector
        # matches them exactly, so normalise (quoted-lowercase owners
        # are vanishingly rare on real targets).
        return (schema or "").upper() or None
    return None  # mysql: current database


def _inspector(engine_or_conn):
    from sqlalchemy import inspect as sa_inspect
    return sa_inspect(engine_or_conn)


def _schema_arg(db_type: str, schema: str | None):
    if (db_type or "").lower() == "mysql":
        return {}
    return {"schema": effective_schema(db_type, schema)}


def list_databases(engine_or_conn, db_type: str, scan_id: str = "-") -> list:
    """Database names for the picker (system/template DBs excluded).

    MySQL connects without a database and runs SHOW DATABASES.
    PostgreSQL connects to the `postgres` maintenance database.
    Oracle lists accessible non-maintained schemas (untested to step 13).
    Reads no table rows.
    """
    from sqlalchemy import text as sa_text
    t = (db_type or "").lower()
    with StepTimer("list_databases", scan_id):
        try:
            if t == "mysql":
                rows = engine_or_conn.execute(sa_text("SHOW DATABASES")).fetchall()
                names = [r[0] for r in rows]
            elif t == "postgresql":
                rows = engine_or_conn.execute(sa_text(
                    "SELECT datname FROM pg_database WHERE NOT datistemplate "
                    "ORDER BY 1")).fetchall()
                names = [r[0] for r in rows]
            elif t == "oracle":
                rows = engine_or_conn.execute(sa_text(
                    "SELECT username FROM all_users "
                    "WHERE oracle_maintained = 'N' ORDER BY 1")).fetchall()
                names = [r[0] for r in rows]
            else:
                raise ValueError("unsupported db_type: %r" % (db_type,))
            out = sorted(n for n in names if n and n.lower() not in EXCLUDED_DATABASES)
        except Exception as e:
            get_logger().error("list_databases failed: %s: %s", type(e).__name__, e,
                               extra={"scan_id": scan_id, "step": "list_databases",
                                      "table": "-"})
            raise ConnectError(map_exception(e, step="list_databases", scan_id=scan_id)) from e
    get_logger().info("databases=%d", len(out),
                      extra={"scan_id": scan_id, "step": "list_databases", "table": "-"})
    return out


def list_tables(engine_or_conn, db_type: str, schema: str | None = None,
                scan_id: str = "-") -> list:
    """Lightweight table list for Connect. Reads no rows."""
    from sqlalchemy import inspect as sa_inspect
    log = get_logger()
    kw = _schema_arg(db_type, schema)
    with StepTimer("list_tables", scan_id):
        try:
            insp = sa_inspect(engine_or_conn)
            names = [n for n in insp.get_table_names(**kw)]
            try:
                views = set(insp.get_view_names(**kw))
            except Exception:
                views = set()
            names = [n for n in names
                     if n not in views and not is_excluded(n, kw.get("schema"))]
            out = []
            for n in sorted(names):
                cols = insp.get_columns(n, **kw)
                try:
                    pk = insp.get_pk_constraint(n, **kw).get("constrained_columns") or []
                except Exception:
                    pk = []
                try:
                    fks = insp.get_foreign_keys(n, **kw)
                except Exception:
                    fks = []
                out.append({"name": n, "columns": len(cols),
                            "has_pk": bool(pk), "fks": len(fks)})
        except Exception as e:
            log.error("list_tables failed: %s: %s", type(e).__name__, e,
                      extra={"scan_id": scan_id, "step": "list_tables", "table": "-"})
            raise ConnectError(map_exception(e, step="list_tables", scan_id=scan_id)) from e
    log.info("tables=%d", len(out),
             extra={"scan_id": scan_id, "step": "list_tables", "table": "-"})
    return out


def table_detail(engine_or_conn, db_type: str, table: str,
                 schema: str | None = None, scan_id: str = "-") -> dict:
    """Full structure of one table: columns, keys, uniques, FKs, indexes."""
    from sqlalchemy import inspect as sa_inspect
    kw = _schema_arg(db_type, schema)
    try:
        insp = sa_inspect(engine_or_conn)
        cols = insp.get_columns(table, **kw)
        pk = insp.get_pk_constraint(table, **kw).get("constrained_columns") or []
        try:
            uniques = insp.get_unique_constraints(table, **kw)
        except Exception:
            uniques = []
        fks = insp.get_foreign_keys(table, **kw)
        try:
            indexes = insp.get_indexes(table, **kw)
        except Exception:
            indexes = []
    except Exception as e:
        get_logger().error("metadata failed for %s: %s: %s", table,
                           type(e).__name__, e,
                           extra={"scan_id": scan_id, "step": "metadata", "table": table})
        raise ConnectError(map_exception(e, step="metadata", scan_id=scan_id)) from e
    pk_set = set(pk)
    fk_by_col = {}
    for fk in fks:
        for c, r in zip(fk.get("constrained_columns") or [],
                        fk.get("referred_columns") or []):
            fk_by_col[c] = {"table": fk.get("referred_table"), "column": r}
    return {
        "name": table,
        "columns": [{"name": c["name"], "type": str(c["type"]),
                     "nullable": bool(c.get("nullable", True)),
                     "pk": c["name"] in pk_set,
                     "fk": fk_by_col.get(c["name"])} for c in cols],
        "pk": list(pk),
        "uniques": [sorted(u.get("column_names") or []) for u in uniques],
        "fks": [{"columns": list(f.get("constrained_columns") or []),
                 "referred_table": f.get("referred_table"),
                 "referred_columns": list(f.get("referred_columns") or [])} for f in fks],
        "indexes": [{"name": i.get("name"),
                     "columns": list(i.get("column_names") or []),
                     "unique": bool(i.get("unique"))} for i in indexes],
    }


def database_structure(engine_or_conn, db_type: str, briefs: list,
                       schema: str | None = None, scan_id: str = "-") -> dict:
    """Details for every table in a Connect list, with step logging."""
    log = get_logger()
    with StepTimer("metadata", scan_id):
        tables = [table_detail(engine_or_conn, db_type, b["name"],
                               schema=schema, scan_id=scan_id) for b in briefs]
    log.info("tables=%d columns=%d fks=%d", len(tables),
             sum(len(t["columns"]) for t in tables),
             sum(len(t["fks"]) for t in tables),
             extra={"scan_id": scan_id, "step": "metadata", "table": "-"})
    return {"tables": tables}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Metadata smoke: table list + structure.")
    ap.add_argument("--db-type", required=True)
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--username", default="root")
    ap.add_argument("--password", default=None)
    ap.add_argument("--database", default="normtest")
    ap.add_argument("--schema", default=None)
    ap.add_argument("--service-name", default=None)
    ap.add_argument("--ssl", action="store_true")
    ap.add_argument("--table", default=None, help="detail for one table only")
    ap.add_argument("--full", action="store_true", help="full structure of all tables")
    ap.add_argument("--list-dbs", action="store_true", help="list databases and exit")
    a = ap.parse_args(argv)
    pw = a.password if a.password is not None else getpass.getpass("Password: ")
    setup_logging()
    p = ConnectParams(db_type=a.db_type, host=a.host, port=a.port,
                      username=a.username, password=pw, database=a.database,
                      schema=a.schema, service_name=a.service_name, ssl=a.ssl)
    try:
        if p.db_type == "postgresql" and not p.database and a.list_dbs:
            p.database = "postgres"
        engine, conn, _ = connect(p, scan_id="smoke")
        try:
            if a.list_dbs:
                out = {"databases": list_databases(conn, p.db_type, scan_id="smoke")}
                print(json.dumps(out, indent=2, default=str))
                return 0
            briefs = list_tables(conn, p.db_type, schema=p.schema, scan_id="smoke")
            if a.table:
                out = table_detail(conn, p.db_type, a.table, schema=p.schema,
                                   scan_id="smoke")
            elif a.full:
                out = database_structure(conn, p.db_type, briefs,
                                         schema=p.schema, scan_id="smoke")
            else:
                out = {"tables": briefs}
        finally:
            close(engine, conn)
    except ConnectError as e:
        print(json.dumps(e.error_obj, indent=2))
        return 2
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
