"""Schema metadata via sqlalchemy.inspect().

Connect step uses list_tables() (name, column count, has-PK, FK count)
and reads no rows. Full structure comes from table_detail().
Views and system schemas are excluded.
"""

import argparse
import getpass
import json

from .db_connect import ConnectError, ConnectParams, assert_select, close, connect, map_exception
from .logging_setup import StepTimer, get_logger, setup_logging

EXCLUDED_SCHEMAS = frozenset({
    "information_schema", "pg_catalog", "pg_toast",
    "mysql", "performance_schema", "sys",
})

EXCLUDED_DATABASES = frozenset({
    "information_schema", "mysql", "performance_schema", "sys",
    "template0", "template1",
    "master", "model", "msdb", "tempdb",
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
    if t == "mssql":
        return schema or None
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
            elif t == "mssql":
                rows = engine_or_conn.execute(sa_text(
                    "SELECT name FROM sys.databases "
                    "WHERE state_desc = 'ONLINE' "
                    "AND name NOT IN ('master','model','msdb','tempdb') "
                    "ORDER BY name")).fetchall()
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
    if (db_type or "").lower() == "oracle":
        return _oracle_table_detail(engine_or_conn, table,
                                    effective_schema(db_type, schema),
                                    scan_id)
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


def _oracle_table_detail(engine_or_conn, table: str, owner: str | None,
                         scan_id: str = "-") -> dict:
    """Dictionary-view metadata with exact-case binds.

    sqlalchemy.inspect() folds unquoted Oracle identifiers to uppercase,
    which misses quoted-lowercase tables (and would mis-resolve any
    mixed-case name). Direct ALL_* queries use the names verbatim.
    Requires catalog access (see docs/OPERATOR_GUIDE.md).
    """
    from sqlalchemy import text as sa_text

    def run(sql, params):
        assert_select(sql)
        return engine_or_conn.execute(sa_text(sql), params).fetchall()

    try:
        cols = run(
            "SELECT column_name, data_type, data_length, data_precision, "
            "data_scale, nullable FROM all_tab_columns "
            "WHERE owner = :o AND table_name = :t ORDER BY column_id",
            {"o": owner, "t": table})
        if not cols:
            from sqlalchemy.exc import NoSuchTableError
            raise NoSuchTableError("%s.%s" % (owner, table))
        pk = [r[0] for r in run(
            "SELECT cc.column_name FROM all_constraints c "
            "JOIN all_cons_columns cc ON cc.owner = c.owner "
            "AND cc.constraint_name = c.constraint_name "
            "WHERE c.owner = :o AND c.table_name = :t "
            "AND c.constraint_type = 'P' ORDER BY cc.position",
            {"o": owner, "t": table})]
        uniq_rows = run(
            "SELECT c.constraint_name, cc.column_name FROM all_constraints c "
            "JOIN all_cons_columns cc ON cc.owner = c.owner "
            "AND cc.constraint_name = c.constraint_name "
            "WHERE c.owner = :o AND c.table_name = :t "
            "AND c.constraint_type = 'U' ORDER BY c.constraint_name, cc.position",
            {"o": owner, "t": table})
        uniques, cur_name, cur_cols = [], None, []
        for cname, col in uniq_rows + [(None, None)]:
            if cname != cur_name:
                if cur_cols:
                    uniques.append(cur_cols)
                cur_name, cur_cols = cname, []
            if col is not None:
                cur_cols.append(col)
        fk_rows = run(
            "SELECT c.constraint_name, cc.column_name, rc.table_name, "
            "rcc.column_name FROM all_constraints c "
            "JOIN all_cons_columns cc ON cc.owner = c.owner "
            "AND cc.constraint_name = c.constraint_name "
            "JOIN all_constraints rc ON rc.owner = c.r_owner "
            "AND rc.constraint_name = c.r_constraint_name "
            "JOIN all_cons_columns rcc ON rcc.owner = rc.owner "
            "AND rcc.constraint_name = rc.constraint_name "
            "AND rcc.position = cc.position "
            "WHERE c.owner = :o AND c.table_name = :t "
            "AND c.constraint_type = 'R' "
            "ORDER BY c.constraint_name, cc.position",
            {"o": owner, "t": table})
        fks, cur_name, cur_fk = [], None, None
        for cname, col, rtab, rcol in fk_rows + [(None, None, None, None)]:
            if cname != cur_name:
                if cur_fk is not None:
                    fks.append(cur_fk)
                cur_fk = None if cname is None else {
                    "columns": [], "referred_table": rtab,
                    "referred_columns": []}
                cur_name = cname
            if cur_fk is not None:
                cur_fk["columns"].append(col)
                cur_fk["referred_columns"].append(rcol)
        idx_rows = run(
            "SELECT i.index_name, ic.column_name, i.uniqueness "
            "FROM all_indexes i JOIN all_ind_columns ic "
            "ON ic.index_owner = i.owner AND ic.index_name = i.index_name "
            "WHERE i.table_owner = :o AND i.table_name = :t "
            "ORDER BY i.index_name, ic.column_position",
            {"o": owner, "t": table})
        indexes, cur_name, cur_idx = [], None, None
        for iname, col, uniq in idx_rows + [(None, None, None)]:
            if iname != cur_name:
                if cur_idx is not None:
                    indexes.append(cur_idx)
                cur_idx = None if iname is None else {
                    "name": iname, "columns": [],
                    "unique": (uniq == "UNIQUE")}
                cur_name = iname
            if cur_idx is not None:
                cur_idx["columns"].append(col)
    except Exception as e:
        get_logger().error("metadata failed for %s: %s: %s", table,
                           type(e).__name__, e,
                           extra={"scan_id": scan_id, "step": "metadata", "table": table})
        from sqlalchemy.exc import NoSuchTableError as _NoSuch
        if isinstance(e, _NoSuch):
            from .errors import make_error as _make_error
            raise ConnectError(_make_error("TABLE_NOT_FOUND", step="metadata",
                                           scan_id=scan_id)) from e
        raise ConnectError(map_exception(e, step="metadata", scan_id=scan_id)) from e
    pk_set = set(pk)
    fk_by_col = {}
    for fk in fks:
        for c, r in zip(fk["columns"], fk["referred_columns"]):
            fk_by_col[c] = {"table": fk["referred_table"], "column": r}
    return {
        "name": table,
        "columns": [{"name": r[0], "type": _oracle_type(r[1], r[2], r[3], r[4]),
                     "nullable": (r[5] != "N"),
                     "pk": r[0] in pk_set,
                     "fk": fk_by_col.get(r[0])} for r in cols],
        "pk": list(pk),
        "uniques": [sorted(u) for u in uniques],
        "fks": fks,
        "indexes": indexes,
    }


def _oracle_type(data_type, length, precision, scale):
    t = (data_type or "").upper()
    if t == "NUMBER" and precision:
        return "NUMBER(%s%s)" % (precision, ",%s" % scale if scale else "")
    if t in ("VARCHAR2", "NVARCHAR2", "CHAR", "NCHAR"):
        return "%s(%s)" % (t, length)
    return t


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
