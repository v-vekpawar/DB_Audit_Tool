"""Step 8 unit level: estimator math and analyzer end-to-end on SQLite
(no server needed). Live-DB acceptance runs through the CLI."""

import threading

import pytest
from sqlalchemy import create_engine, text

from app.analyzer import analyze_on_connection, headline
from app.estimator import Estimator, work_units


class P:
    db_type = "mysql"
    host = "localhost"
    database = "test"
    schema = None


@pytest.fixture()
def sqlite_conn():
    e = create_engine("sqlite://")
    with e.begin() as c:
        c.execute(text("CREATE TABLE orders (id INTEGER PRIMARY KEY, cid INT, cn VARCHAR(20), q INT)"))
        for i in range(120):
            cid = (i * 7) % 40 + 1
            c.execute(text("INSERT INTO orders VALUES (:i, :c, :n, :q)"),
                      {"i": i, "c": cid, "n": "C%03d" % cid, "q": i % 5})
        c.execute(text("CREATE TABLE cleanone (id INTEGER PRIMARY KEY, v INT, w VARCHAR(8))"))
        for i in range(60):
            c.execute(text("INSERT INTO cleanone VALUES (:i, :v, :w)"),
                      {"i": i, "v": (i * 13) % 97, "w": "w%d" % ((i * 7) % 9)})
        c.execute(text("CREATE TABLE tiny (a INT, b VARCHAR(4))"))
        for r in [(1, "x"), (1, "x"), (2, "y"), (2, "y"), (3, "z")]:
            c.execute(text("INSERT INTO tiny VALUES (:a, :b)"), {"a": r[0], "b": r[1]})
        c.execute(text("CREATE TABLE ref (id INTEGER PRIMARY KEY, code VARCHAR(8))"))
        for i in range(1, 21):
            c.execute(text("INSERT INTO ref VALUES (:i, :c)"),
                      {"i": i, "c": "C%03d" % i})
        c.execute(text("CREATE TABLE cop (id INTEGER PRIMARY KEY, rid INT NOT NULL REFERENCES ref(id), rcode VARCHAR(8))"))
        for i in range(60):
            rid = (i % 20) + 1
            c.execute(text("INSERT INTO cop VALUES (:i, :r, :c)"),
                      {"i": i, "r": rid, "c": "C%03d" % rid})
    conn = e.connect()
    yield e, conn
    conn.close()


def test_work_units():
    assert work_units(100, 10, 90) == 10000
    assert work_units(0, 10, 90) == 0


def test_estimator_blends_and_labels():
    est = Estimator({"a": 1000.0, "b": 1000.0, "c": 1000.0,
                     "d": 1000.0},
                    default_speed=0.001, prior_units=1000.0)
    first = est.estimate(["a", "b", "c", "d"])
    assert first["basis"] == "rough"
    est.observe("a", 2.0)  # measured 0.002/s/u vs default 0.001
    est.observe("b", 2.0)
    assert 0.001 < est.speed() < 0.002
    est.observe("c", 2.0)
    assert est.estimate(["d"])["basis"] == "measured"
    assert est.estimate(["d"])["remaining_seconds"] > 0


def test_estimator_unknown_stays_rough():
    est = Estimator({"a": 1000.0, "b": None}, default_speed=0.001,
                    prior_units=1000.0)
    for t in ("a", "x", "y"):
        est.observe(t, 1.0)
    assert est.estimate(["b"])["basis"] == "rough"


def test_headline_format():
    h = headline({"faulted": 3, "suspected": 1, "insufficient_data": 1,
                  "clean": 7, "error": 0, "not_run": 0}, 12, 30)
    assert h == "12 of 42 tables scanned (30 not selected): 3 faulted, 1 suspected, 1 insufficient data, 7 clean"
    mid = headline({"faulted": 1, "suspected": 0, "insufficient_data": 0,
                    "clean": 0, "error": 0, "not_run": 0}, 12, 30)
    assert mid == "1 of 42 tables scanned (30 not selected): 1 faulted"


def test_scan_end_to_end(sqlite_conn):
    e, conn = sqlite_conn
    events = []
    out = analyze_on_connection(e, conn, P(), ["orders", "cleanone", "tiny"],
                                scan_id="t1", on_event=events.append)
    assert out["scan"]["status"] == "completed"
    by_name = {s["name"]: s for s in out["table_summary"]}
    assert by_name["orders"]["status"] == "faulted"
    assert by_name["orders"]["normal_form"] == "2NF"
    assert by_name["tiny"]["status"] == "insufficient_data"
    full = {t["name"]: t for t in out["tables"]}
    assert "orders" in full and "cleanone" not in full
    kinds = {e["event"] for e in events}
    assert {"scan_started", "estimate", "table_started", "table_step",
            "table_done", "scan_done"} <= kinds
    assert out["scan"]["counts"]["clean"] >= 1


def test_missing_table_is_error_not_clean(sqlite_conn):
    e, conn = sqlite_conn
    out = analyze_on_connection(e, conn, P(), ["orders", "nosuchtable"],
                                scan_id="t2")
    by_name = {s["name"]: s for s in out["table_summary"]}
    assert by_name["nosuchtable"]["status"] == "error"
    assert by_name["orders"]["status"] == "faulted"
    assert out["notices"] and out["notices"][0]["table"] == "nosuchtable"
    assert "clean" not in [s["status"] for s in out["table_summary"]
                           if s["name"] == "nosuchtable"]


def test_empty_selection_is_validation_error(sqlite_conn):
    e, conn = sqlite_conn
    out = analyze_on_connection(e, conn, P(), [], scan_id="t3")
    assert out["scan"]["status"] == "failed"
    assert out["error"]["code"] == "VALIDATION_ERROR"


def test_cross_table_evidence_carries_supported_flag(sqlite_conn):
    e, conn = sqlite_conn
    out = analyze_on_connection(e, conn, P(), ["cop"], scan_id="t5")
    by_name = {s["name"]: s for s in out["table_summary"]}
    assert by_name["cop"]["status"] == "faulted"
    full = {t["name"]: t for t in out["tables"]}["cop"]
    fds = [f for f in full["fds"] if f["determinant"] == ["rid"]]
    assert fds, "expected the FK-copy FD to be reported"
    assert fds[0]["evidence"]["cross_table"]["supported"] is True
    assert fds[0]["evidence"]["cross_table"]["ref_table"] == "ref"


def test_stop_leaves_not_run(sqlite_conn):
    e, conn = sqlite_conn
    ev = threading.Event()
    ev.set()
    out = analyze_on_connection(e, conn, P(), ["orders", "cleanone"],
                                scan_id="t4", stop_event=ev)
    assert out["scan"]["status"] == "stopped"
    assert [s["status"] for s in out["table_summary"]] == ["not_run", "not_run"]
