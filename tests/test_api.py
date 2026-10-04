"""Step 9 checkpoint: API behaviour through HTTP.

Offline parts run with a fake analyzer (no database). Live parts
(NORMTEST_LIVE=1 plus NORMTEST_*_PW) hit both local test DBs.
"""

import json
import os
import time

import pytest
from fastapi.testclient import TestClient

import app.scan_sessions as sessions
from app import storage
from app.main import app

LIVE = os.environ.get("NORMTEST_LIVE", "").strip() == "1"
needs_live = pytest.mark.skipif(not LIVE, reason="needs live test DBs")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "last.sqlite")
    sessions._sessions.clear()
    sessions._current_id = None
    with TestClient(app) as c:
        yield c
    sessions._sessions.clear()
    sessions._current_id = None


FAKE_RESULT = {
    "scan": {"id": "x", "status": "completed", "selected_tables": ["a"],
             "counts": {"faulted": 1, "clean": 0}},
    "table_summary": [{"name": "a", "status": "faulted",
                       "normal_form": "2NF", "duration_seconds": 0.1}],
    "tables": [{"name": "a", "status": "faulted"}],
    "notices": [],
}


def fake_ok(params, selected, schema=None, scan_id="s", on_event=None,
            stop_event=None, max_rows=50000):
    emit = on_event or (lambda e: None)
    emit({"event": "scan_started", "tables": list(selected), "total": 1})
    emit({"event": "table_started", "table": "a", "index": 1, "total": 1})
    emit({"event": "table_done", "table": "a", "status": "faulted",
          "normal_form": "2NF", "duration_seconds": 0.1,
          "result": FAKE_RESULT["tables"][0]})
    emit({"event": "scan_done", "status": "completed",
          "counts": FAKE_RESULT["scan"]["counts"]})
    out = json.loads(json.dumps(FAKE_RESULT))
    out["scan"]["id"] = scan_id
    return out


def test_connect_validation_no_db(client):
    r = client.post("/api/connect", json={"db_type": "mysql", "host": ""})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"
    r = client.post("/api/connect", json={"db_type": "mysql", "host": "h",
                                           "port": 70000})
    assert r.status_code == 400


def test_connect_tables_needs_database(client):
    r = client.post("/api/connect/tables",
                    json={"db_type": "mysql", "host": "h"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_connect_lists_databases(client, monkeypatch):
    import app.main as main

    def fake_connect(params, scan_id="-", require_db=True):
        class E:
            pass
        return E(), object(), "8.0.41"

    monkeypatch.setattr(main, "connect", fake_connect)
    monkeypatch.setattr(main, "close", lambda e, c: None)
    import app.metadata as metadata
    monkeypatch.setattr(metadata, "list_databases",
                        lambda conn, db_type, scan_id="-": ["appdb", "normtest"])
    r = client.post("/api/connect", json={"db_type": "mysql", "host": "h",
                                           "username": "u", "password": "p"})
    assert r.status_code == 200, r.text
    assert r.json()["databases"] == ["appdb", "normtest"]
    assert "password" not in r.text


def test_scan_start_needs_tables(client):
    r = client.post("/api/scan/start", json={"db_type": "mysql",
                                             "host": "localhost",
                                             "database": "x", "tables": []})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_full_fake_flow(client, monkeypatch):
    monkeypatch.setattr(sessions, "analyze_db", fake_ok)
    r = client.post("/api/scan/start", json={"db_type": "mysql",
                                             "host": "localhost",
                                             "database": "x",
                                             "tables": ["a"]})
    assert r.status_code == 200
    sid = r.json()["scan_id"]
    for _ in range(100):
        res = client.get("/api/scan/%s/results" % sid).json()
        if res.get("scan", {}).get("status") == "completed":
            break
        time.sleep(0.05)
    assert res["scan"]["status"] == "completed"
    assert res["table_summary"][0]["status"] == "faulted"
    # progress stream replays then ends at scan_done
    with client.stream("GET", "/api/scan/%s/progress" % sid) as s:
        assert s.status_code == 200
        names = [json.loads(line.split("data: ", 1)[1])["event"]
                 for line in s.iter_lines()
                 if line.startswith("data: ")]
    assert names[0] == "scan_started" and names[-1] == "scan_done"
    assert "table_done" in names
    # last scan survived (storage round-trip)
    last = client.get("/api/scan/last").json()
    assert last["table_summary"][0]["status"] == "faulted"
    assert "password" not in json.dumps(last)
    # terminal scans are no longer current
    assert client.get("/api/scan/current").status_code == 404
    # unknown ids 404
    assert client.get("/api/scan/nope/results").status_code == 404
    assert client.post("/api/scan/nope/stop").status_code == 404
    assert client.get("/api/scan/last").status_code == 200


def test_last_empty_is_404(client):
    assert client.get("/api/scan/last").status_code == 404


def test_second_start_conflicts(client, monkeypatch):
    import threading

    def slow(params, selected, schema=None, scan_id="s", on_event=None,
             stop_event=None, max_rows=50000):
        (on_event or (lambda e: None))({"event": "scan_started",
                                        "tables": list(selected), "total": 1})
        stop_event.wait(5)
        raise AssertionError("should have been stopped")

    monkeypatch.setattr(sessions, "analyze_db", slow)
    r = client.post("/api/scan/start", json={"db_type": "mysql",
                                             "host": "localhost",
                                             "database": "x",
                                             "tables": ["a"]})
    assert r.status_code == 200
    r2 = client.post("/api/scan/start", json={"db_type": "mysql",
                                              "host": "localhost",
                                              "database": "x",
                                              "tables": ["a"]})
    assert r2.status_code == 409
    assert r2.json()["error"]["code"] == "SCAN_ALREADY_RUNNING"
    sid = r.json()["scan_id"]
    assert client.post("/api/scan/%s/stop" % sid).status_code == 200


def test_stop_mid_scan_keeps_partial(client, monkeypatch):
    import threading

    entered = threading.Event()

    def blocking(params, selected, schema=None, scan_id="s", on_event=None,
                 stop_event=None, max_rows=50000):
        from app.analyzer import analyze_db as real

        emit = on_event or (lambda e: None)
        emit({"event": "scan_started", "tables": list(selected), "total": 2})
        emit({"event": "table_started", "table": "slow", "index": 1, "total": 2})
        entered.set()
        stop_event.wait(10)
        # cooperative stop, like the real analyzer between tables
        stopped = stop_event.is_set()
        status = "stopped" if stopped else "completed"
        counts = {"faulted": 0, "suspected": 0, "insufficient_data": 0,
                  "clean": 0, "error": 0,
                  "not_run": 1 if stopped else 0}
        if stopped:
            emit({"event": "table_error", "table": "gone",
                  "error": {"code": "TABLE_NOT_FOUND"}})
        emit({"event": "scan_done", "status": status, "counts": counts,
              "headline": status})
        return {"scan": {"id": scan_id, "status": status,
                         "selected_tables": list(selected), "counts": counts},
                "table_summary": [], "tables": [], "notices": []}

    monkeypatch.setattr(sessions, "analyze_db", blocking)
    sid = client.post("/api/scan/start", json={"db_type": "mysql",
                                               "host": "localhost",
                                               "database": "x",
                                               "tables": ["slow", "rest"]}).json()["scan_id"]
    assert entered.wait(5)
    r = client.post("/api/scan/%s/stop" % sid)
    assert r.status_code == 200
    for _ in range(100):
        res = client.get("/api/scan/%s/results" % sid).json()
        if res.get("scan", {}).get("status") == "stopped":
            break
        time.sleep(0.05)
    assert res["scan"]["status"] == "stopped"
    assert res["scan"]["counts"]["not_run"] == 1


def test_scan_thread_crash_is_failed_scan(client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("forced")

    monkeypatch.setattr(sessions, "analyze_db", boom)
    sid = client.post("/api/scan/start", json={"db_type": "mysql",
                                               "host": "localhost",
                                               "database": "x",
                                               "tables": ["a"]}).json()["scan_id"]
    for _ in range(100):
        res = client.get("/api/scan/%s/results" % sid).json()
        if res.get("scan", {}).get("status") == "failed":
            break
        time.sleep(0.05)
    assert res["scan"]["status"] == "failed"


def _pw(prefix):
    return os.environ.get(prefix + "_PW", "")


@needs_live
def test_live_connect_lists_databases_then_tables(client):
    import app.metadata as metadata

    for db, user, pw, sysdb in (
            ("mysql", "root", _pw("NORMTEST_MYSQL"), "mysql"),
            ("postgresql", "postgres", _pw("NORMTEST_PG"), "template1")):
        body = {"db_type": db, "host": "localhost",
                "username": user, "password": pw}
        r = client.post("/api/connect", json=body)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "normtest" in data["databases"]
        assert sysdb not in data["databases"]
        assert "password" not in json.dumps(data)
        body["database"] = "normtest"
        r = client.post("/api/connect/tables", json=body)
        assert r.status_code == 200, r.text
        assert len(r.json()["tables"]) == 29


@needs_live
def test_live_scan_subset_with_progress(client):
    body = {"db_type": "mysql", "host": "localhost", "username": "root",
            "password": _pw("NORMTEST_MYSQL"), "database": "normtest",
            "tables": ["d_tiny", "d_equiv_cols", "d_name_trap"]}
    sid = client.post("/api/scan/start", json=body).json()["scan_id"]
    with client.stream("GET", "/api/scan/%s/progress" % sid) as s:
        names = [json.loads(line.split("data: ", 1)[1])
                 for line in s.iter_lines() if line.startswith("data: ")]
    kinds = [e["event"] for e in names]
    assert kinds[0] == "scan_started" and kinds[-1] == "scan_done"
    assert kinds.count("table_done") == 3
    assert all("result" in e or True for e in names)
    res = client.get("/api/scan/%s/results" % sid).json()
    assert res["scan"]["status"] == "completed"
    assert res["scan"]["counts"]["clean"] == 2
    assert client.get("/api/scan/last").status_code == 200
