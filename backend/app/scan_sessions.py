"""Scan sessions: lifecycle, event log, SSE fan-out, stop, crash guard.

One scan runs at a time. Credentials live in the session object only for
the scan's duration and are wiped when it ends. The background thread is
wrapped so any crash ends the scan as failed with a logged error object.
"""

import queue
import threading
import uuid
from dataclasses import dataclass, field

from .analyzer import analyze_db
from .db_connect import ConnectError, ConnectParams
from .errors import make_error
from .logging_setup import get_logger


@dataclass
class Session:
    scan_id: str
    params: ConnectParams
    selected: list
    schema: str | None
    status: str = "running"
    events: list = field(default_factory=list)
    queues: list = field(default_factory=list)
    stop_event: threading.Event = field(default_factory=threading.Event)
    result: dict | None = None
    error: dict | None = None
    seq: int = 0

    def emit(self, ev: dict) -> dict:
        self.seq += 1
        full = {"seq": self.seq, **ev}
        self.events.append(full)
        for q in self.queues:
            try:
                q.put_nowait(full)
            except queue.Full:
                pass
        if ev.get("event") == "scan_done":
            self.status = ev.get("status", "completed")
        return full

    def partial_result(self) -> dict:
        tables = [e["result"] for e in self.events
                  if e.get("event") == "table_done" and e.get("result")]
        summary = []
        counts = {"faulted": 0, "suspected": 0, "insufficient_data": 0,
                  "clean": 0, "error": 0, "not_run": 0}
        for e in self.events:
            if e.get("event") == "table_done":
                summary.append({"name": e["table"], "status": e["status"],
                                "normal_form": e.get("normal_form"),
                                "duration_seconds": e.get("duration_seconds", 0.0)})
                counts[e["status"]] = counts.get(e["status"], 0) + 1
            elif e.get("event") == "table_error":
                summary.append({"name": e["table"], "status": "error",
                                "normal_form": None, "duration_seconds": 0.0})
                counts["error"] += 1
        return {"scan": {"id": self.scan_id, "status": self.status,
                         "selected_tables": list(self.selected),
                         "counts": counts},
                "table_summary": summary, "tables": tables, "notices": []}


_sessions: dict = {}
_current_id: str | None = None
_lock = threading.Lock()


def get_session(scan_id: str) -> Session | None:
    with _lock:
        return _sessions.get(scan_id)


def current_session() -> Session | None:
    with _lock:
        s = _sessions.get(_current_id) if _current_id else None
    return s if s is not None and s.status == "running" else None


def start_scan(params: ConnectParams, selected: list,
               schema: str | None) -> Session:
    global _current_id
    with _lock:
        cur = _sessions.get(_current_id) if _current_id else None
        if cur is not None and cur.status == "running":
            raise ConnectError(make_error("SCAN_ALREADY_RUNNING", step="scan",
                                          scan_id=cur.scan_id))
        scan_id = uuid.uuid4().hex[:8]
        session = Session(scan_id=scan_id, params=params,
                          selected=list(selected), schema=schema)
        _sessions[scan_id] = session
        _current_id = scan_id
    thread = threading.Thread(target=_run_scan, args=(scan_id,),
                              daemon=True, name="scan-" + scan_id)
    thread.start()
    return session


def stop_scan(scan_id: str) -> Session | None:
    s = get_session(scan_id)
    if s is not None and s.status == "running":
        s.stop_event.set()
    return s


def _run_scan(scan_id: str) -> None:
    log = get_logger()
    session = get_session(scan_id)
    if session is None:
        return
    try:
        result = analyze_db(session.params, session.selected,
                            schema=session.schema, scan_id=scan_id,
                            on_event=session.emit,
                            stop_event=session.stop_event)
        session.result = result
        if result.get("scan", {}).get("status") in ("completed", "stopped"):
            try:
                from .storage import save_last_scan
                save_last_scan(result, scan_id=scan_id)
            except Exception as e:
                log.error("last scan not saved: %s: %s", type(e).__name__, e,
                          extra={"scan_id": scan_id, "step": "save", "table": "-"})
    except ConnectError as e:
        log.error("scan thread failed: %s", e.error_obj["error"]["code"],
                  extra={"scan_id": scan_id, "step": "scan", "table": "-"})
        session.error = e.error_obj
        session.result = {"scan": {"id": scan_id, "status": "failed"},
                          "table_summary": [], "tables": [], "notices": []}
        session.emit({"event": "scan_done", "status": "failed",
                      "error": e.error_obj, "counts": {}})
    except Exception as e:
        log.exception("scan thread crashed: %s", e,
                      extra={"scan_id": scan_id, "step": "scan", "table": "-"})
        err = make_error("INTERNAL_ERROR", step="scan",
                         scan_id=scan_id)["error"]
        session.error = err
        session.result = {"scan": {"id": scan_id, "status": "failed"},
                          "table_summary": [], "tables": [], "notices": []}
        session.emit({"event": "scan_done", "status": "failed",
                      "error": err, "counts": {}})
    finally:
        try:
            session.params.password = ""
        except Exception:
            pass
