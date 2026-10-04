"""Step 12 checkpoint: PDF content for every violation kind, API
delivery without disk writes, bad-format and unknown-id handling."""

import json
from pathlib import Path

from app.export_pdf import build_pdf

FIXTURE = json.load(open(Path(__file__).resolve().parent.parent
                         / "frontend" / "tests" / "fixture-result.json",
                         encoding="utf-8"))


def wrap():
    return {"scan": {"id": "pdf1", "db_type": "mysql", "database": "shop",
                     "started_at": "2026-10-04T12:00:00+00:00",
                     "duration_seconds": 42.0, "status": "completed",
                     "selected_tables": [t["name"] for t in FIXTURE["tables"]],
                     "counts": {"total_in_db": 5, "selected": 5,
                                "not_selected": 0, "scanned": 5, "faulted": 4,
                                "suspected": 1, "clean": 0, "error": 0,
                                "not_run": 0}},
            "table_summary": [], "tables": FIXTURE["tables"], "notices": []}


def test_pdf_covers_every_kind():
    pdf = build_pdf(wrap(), compress=False)
    assert pdf.startswith(b"%PDF")
    text = pdf.decode("latin-1")
    for needle in ("t_3nf_transitive", "t_2nf_partial", "t_1nf_delimited",
                   "t_1nf_repeating", "t_suspected", "Confidence Score: 96",
                   "Why this fails 3NF", "Recommendation:",
                   "possibly 2NF", "comma-separated lists",
                   "repeating group", "holds with exceptions",
                   "\\(exception\\)", "98.4% match", "sampled"):
        assert needle in text, needle


def test_pdf_empty_result():
    pdf = build_pdf({"scan": {"id": "e", "status": "completed",
                               "selected_tables": [],
                               "counts": {"not_selected": 0}},
                     "table_summary": [], "tables": [], "notices": []})
    assert pdf.startswith(b"%PDF") and len(pdf) > 500


def test_export_endpoint_no_disk_writes(client, monkeypatch):
    import app.scan_sessions as sessions

    backend = Path(__file__).resolve().parent.parent / "backend"
    before = {p.name for p in backend.iterdir()}

    sid_holder = {}

    def fake(params, selected, schema=None, scan_id="s", on_event=None,
             stop_event=None, max_rows=50000):
        (on_event or (lambda e: None))({"event": "scan_started",
                                        "tables": ["a"], "total": 1})
        (on_event or (lambda e: None))({"event": "scan_done",
                                        "status": "completed", "counts": {}})
        out = wrap()
        out["scan"]["id"] = scan_id
        return out

    monkeypatch.setattr(sessions, "analyze_db", fake)
    sid = client.post("/api/scan/start", json={"db_type": "mysql",
                                               "host": "localhost",
                                               "database": "x",
                                               "tables": ["a"]}).json()["scan_id"]
    sid_holder["id"] = sid
    import time
    for _ in range(100):
        if client.get("/api/scan/%s/results" % sid).json()["scan"]["status"] == "completed":
            break
        time.sleep(0.05)
    r = client.get("/api/scan/%s/export?format=pdf" % sid)
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF")
    assert "attachment" in r.headers["content-disposition"]
    assert {p.name for p in backend.iterdir()} == before
    assert client.get("/api/scan/%s/export?format=xlsx" % sid).status_code == 400
    assert client.get("/api/scan/nope/export?format=pdf").status_code == 404
