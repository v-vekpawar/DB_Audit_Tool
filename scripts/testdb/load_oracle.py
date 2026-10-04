"""Create the Oracle test user and load the planted test database.

Operator-run (needs the Docker Oracle from docs/TEST_PLAN.md running):

  python scripts/testdb/load_oracle.py --password Testpw123

Uses the same Python row semantics as render_oracle.py (executemany with
binds, no SQL parsing). Idempotent: drops and recreates everything.
Needs oracledb (in requirements.txt).
"""

import argparse
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def rows_of(t):
    import render_oracle as O

    return O.table_rows(t)


def convert(value, typename):
    if value is None:
        return None
    if isinstance(value, tuple) and len(value) == 2 and isinstance(value[1], int):
        base, offset = value  # dateoff marker: (base-date-string, days)
        return datetime.date.fromisoformat(base) + datetime.timedelta(days=offset)
    if typename == "BLOB" and isinstance(value, str):
        return value.encode("utf-8")
    return value


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=1521)
    ap.add_argument("--service", default="FREEPDB1")
    ap.add_argument("--system-user", default="system")
    ap.add_argument("--password", required=True,
                    help="SYSTEM password (== ORACLE_PASSWORD); also used for tester")
    ap.add_argument("--tester", default="tester")
    a = ap.parse_args()

    try:
        import oracledb
    except ImportError:
        print("oracledb is not installed in this venv", file=sys.stderr)
        return 1
    import render_oracle as O
    import render_planted as R

    dsn = "%s:%d/%s" % (a.host, a.port, a.service)

    print("== phase 1: test user (as %s)" % a.system_user)
    sys_conn = oracledb.connect(user=a.system_user, password=a.password, dsn=dsn)
    with sys_conn.cursor() as cur:
        cur.execute("ALTER SESSION SET CONTAINER = %s" % a.service)
        try:
            cur.execute('CREATE USER %s IDENTIFIED BY "%s"' % (a.tester, a.password.replace('"', '')))
            print("user created")
        except Exception as e:
            if "ORA-01920" not in str(e):
                raise
            print("user exists: dropping and recreating")
            cur.execute("DROP USER %s CASCADE" % a.tester)
            cur.execute('CREATE USER %s IDENTIFIED BY "%s"' % (a.tester, a.password.replace('"', '')))
        cur.execute("GRANT CREATE SESSION, CREATE TABLE, UNLIMITED TABLESPACE TO %s" % a.tester)
    sys_conn.commit()
    sys_conn.close()
    # Cosmetic only: real version string in the UI (scans fall back to
    # "unknown" without it, by design). Needs SYSDBA; never blocks setup.
    try:
        dba = oracledb.connect(user="sys", password=a.password, dsn=dsn,
                               mode=oracledb.AUTH_MODE_SYSDBA)
        with dba.cursor() as cur:
            cur.execute("ALTER SESSION SET CONTAINER = %s" % a.service)
            cur.execute("GRANT SELECT ON SYS.V_$VERSION TO %s" % a.tester)
        dba.commit()
        dba.close()
        print("version grant ok")
    except Exception as e:
        print("WARNING: version grant skipped (%s); scans show version unknown" % e)

    R.define_tables()
    names = {t["name"] for t in R.TABLES} | {"d_tiny"}
    for t in R.TABLES:
        names.update(c[0] for c in t["cols"])
    names.update(("a", "b", "c", "d", "e", "f_note"))

    print("== phase 2: tables and rows (as %s)" % a.tester)
    conn = oracledb.connect(user=a.tester, password=a.password, dsn=dsn)
    total = 0
    with conn.cursor() as cur:
        for name in reversed([t["name"] for t in R.TABLES] + ["d_tiny"]):
            try:
                cur.execute('DROP TABLE "%s" CASCADE CONSTRAINTS' % name)
            except Exception as e:
                if "ORA-00942" not in str(e):
                    raise
        tiny = {"name": "d_tiny",
                "cols": [("a", "INT"), ("b", "VARCHAR(8)"), ("c", "INT"),
                         ("d", "VARCHAR(8)"), ("e", "INT"), ("f_note", "VARCHAR(20)")]}
        cur.execute("CREATE TABLE %s (\n  %s INT, %s VARCHAR2(8), %s INT, "
                    "%s VARCHAR2(8), %s INT, %s VARCHAR2(20)\n)" % (
                        '"d_tiny"', '"a"', '"b"', '"c"', '"d"', '"e"', '"f_note"'))
        for t in R.TABLES:
            cur.execute(O.ddl(t, names))
        for t in [tiny] + R.TABLES:
            rows = rows_of(t) if t["name"] != "d_tiny" else [
                (1, "x", 10, "p", 100, "n1"), (1, "x", 20, "q", 101, "n2"),
                (2, "y", 10, "p", 102, "n3"), (2, "y", 30, "r", 103, "n4"),
                (3, "z", 10, "p", 104, "n5")]
            types = [c[1].split("(")[0].upper() for c in t["cols"]]
            cols = ", ".join('"%s"' % c[0] for c in t["cols"])
            ph = ", ".join(":%d" % (k + 1) for k in range(len(types)))
            data = [tuple(convert(v, types[k]) for k, v in enumerate(r)) for r in rows]
            cur.executemany('INSERT INTO "%s" (%s) VALUES (%s)' % (t["name"], cols, ph), data)
            total += len(data)
            print("  %-22s %6d" % (t["name"], len(data)))
    conn.commit()

    print("== phase 3: verify counts")
    ok = True
    with conn.cursor() as cur:
        for t in [tiny] + R.TABLES:
            (n,) = cur.execute('SELECT COUNT(*) FROM "%s"' % t["name"]).fetchone()
            want = 5 if t["name"] == "d_tiny" else next(x["n"] for x in R.TABLES if x["name"] == t["name"])
            mark = "ok" if n == want else "MISMATCH want %d" % want
            ok &= n == want
            print("  %-22s %6d %s" % (t["name"], n, mark))
        print(conn.version)
    conn.close()
    print("total rows: %d" % total)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
