"""FastAPI app: connect, scan lifecycle, SSE progress, results, last scan.

Serves the frontend from ../frontend when it exists (build step 10).
Binds to 127.0.0.1 via the uvicorn command line; no CORS.
"""

import json
import queue
import uuid
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from . import config, scan_sessions
from .db_connect import (
    ConnectError,
    ConnectParams,
    close,
    connect,
    default_port,
    normalize_db_type,
)
from .errors import make_error
from .logging_setup import get_logger, setup_logging
from .metadata import list_tables
from .schemas import ConnectRequest, ScanStartRequest

app = FastAPI(title="DB Normalization Checker")


@app.exception_handler(RequestValidationError)
async def _validation_handler(request: Request, exc: RequestValidationError):
    return _err("VALIDATION_ERROR", "scan", "-", detail=str(exc.errors()[0].get("msg", "?")))


@app.exception_handler(Exception)
async def _internal_handler(request: Request, exc: Exception):
    get_logger().exception("request crashed: %s %s: %s", request.method,
                           request.url.path, exc,
                           extra={"scan_id": "-", "step": "api", "table": "-"})
    return _err("INTERNAL_ERROR", "api", "-")


def _err(code, step, scan_id, detail=""):
    from .errors import http_status
    err = make_error(code, step=step, scan_id=scan_id, detail=detail)["error"]
    return JSONResponse(status_code=http_status(code), content={"error": err})


def _params_from(body: ConnectRequest | ScanStartRequest, op: str,
                  require_db: bool = True):
    try:
        db_type = normalize_db_type(body.db_type)
    except ValueError:
        raise ConnectError(make_error(
            "VALIDATION_ERROR", step=op, scan_id="-",
            detail="db_type must be mysql, postgresql or oracle"))
    host = (body.host or "").strip()
    if not host:
        raise ConnectError(make_error(
            "VALIDATION_ERROR", step=op, scan_id="-",
            detail="host is required"))
    port = body.port or 0
    if port and not 1 <= port <= 65535:
        raise ConnectError(make_error(
            "VALIDATION_ERROR", step=op, scan_id="-",
            detail="port must be 1-65535"))
    params = ConnectParams(db_type=db_type, host=host,
                           port=port or default_port(db_type),
                           username=body.username or "",
                           password=body.password or "",
                           database=(body.database or "").strip(),
                           schema=(body.db_schema or "").strip() or None,
                           service_name=(body.service_name or "").strip() or None,
                           ssl=bool(body.ssl))
    try:
        from .db_connect import build_url
        build_url(params, require_db=require_db)
    except ValueError as e:
        raise ConnectError(make_error(
            "VALIDATION_ERROR", step=op, scan_id="-", detail=str(e)))
    return params


@app.get("/api/health")
def health():
    return {"ok": True}


@app.post("/api/connect")
def api_connect(body: ConnectRequest):
    """Connect without a database; return the database picker list."""
    scan_id = uuid.uuid4().hex[:8]
    try:
        params = _params_from(body, "connect", require_db=False)
        if params.db_type == "postgresql" and not params.database:
            params.database = "postgres"
    except ConnectError as e:
        e.error_obj["error"]["scan_id"] = scan_id
        from .errors import http_status
        return JSONResponse(status_code=http_status(e.error_obj["error"]["code"]),
                            content=e.error_obj)
    try:
        engine, conn, version = connect(params, scan_id=scan_id,
                                        require_db=False)
        try:
            from .metadata import list_databases
            databases = list_databases(conn, params.db_type, scan_id=scan_id)
        finally:
            close(engine, conn)
    except ConnectError as e:
        from .errors import http_status
        return JSONResponse(status_code=http_status(e.error_obj["error"]["code"]),
                            content=e.error_obj)
    if not databases:
        err = make_error("NO_TABLES", step="connect", scan_id=scan_id)["error"]
        return JSONResponse(status_code=502, content={"error": err})
    return {"scan_id": scan_id, "db_type": params.db_type,
            "db_version": version, "databases": databases}


@app.post("/api/connect/tables")
def api_connect_tables(body: ConnectRequest):
    """Table list for the one picked database (the old Connect step)."""
    scan_id = uuid.uuid4().hex[:8]
    try:
        params = _params_from(body, "connect", require_db=True)
    except ConnectError as e:
        e.error_obj["error"]["scan_id"] = scan_id
        from .errors import http_status
        return JSONResponse(status_code=http_status(e.error_obj["error"]["code"]),
                            content=e.error_obj)
    try:
        engine, conn, version = connect(params, scan_id=scan_id)
        try:
            briefs = list_tables(conn, params.db_type, schema=params.schema,
                                 scan_id=scan_id)
        finally:
            close(engine, conn)
    except ConnectError as e:
        from .errors import http_status
        return JSONResponse(status_code=http_status(e.error_obj["error"]["code"]),
                            content=e.error_obj)
    if not briefs:
        err = make_error("NO_TABLES", step="connect", scan_id=scan_id)["error"]
        return JSONResponse(status_code=502, content={"error": err})
    schema = params.schema or (config.DEFAULT_POSTGRES_SCHEMA
                               if params.db_type == "postgresql" else None)
    return {"scan_id": scan_id, "db_type": params.db_type,
            "db_version": version, "schema": schema, "tables": briefs}


@app.post("/api/scan/start")
def api_scan_start(body: ScanStartRequest):
    try:
        params = _params_from(body, "scan")
    except ConnectError as e:
        from .errors import http_status
        return JSONResponse(status_code=http_status(e.error_obj["error"]["code"]),
                            content=e.error_obj)
    tables = [t for t in (body.tables or []) if str(t).strip()]
    if not tables:
        return _err("VALIDATION_ERROR", "scan", "-", "no table ticked")
    try:
        session = scan_sessions.start_scan(params, tables, params.schema)
    except ConnectError as e:
        from .errors import http_status
        return JSONResponse(status_code=http_status(e.error_obj["error"]["code"]),
                            content=e.error_obj)
    return {"scan_id": session.scan_id}


def _sse(ev: dict) -> str:
    return "event: %s\ndata: %s\n\n" % (ev.get("event", "message"),
                                        json.dumps(ev, default=str))


@app.get("/api/scan/{scan_id}/progress")
async def api_progress(scan_id: str, since: int = 0):
    session = scan_sessions.get_session(scan_id)
    if session is None:
        return _err("SCAN_NOT_FOUND", "scan", scan_id)

    async def gen():
        yield "retry: 3000\n: connected\n\n"
        q: queue.Queue = queue.Queue()
        session.queues.append(q)
        try:
            cur = session.seq
            for ev in session.events:
                if since < ev["seq"] <= cur:
                    yield _sse(ev)
                    if ev.get("event") == "scan_done":
                        return
            while True:
                try:
                    ev = await run_in_threadpool(q.get, True,
                                                 config.SSE_HEARTBEAT_SECONDS)
                except queue.Empty:
                    yield ": heartbeat\n\n"
                    continue
                if ev["seq"] <= cur:
                    continue
                yield _sse(ev)
                if ev.get("event") == "scan_done":
                    return
        finally:
            try:
                session.queues.remove(q)
            except ValueError:
                pass

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.get("/api/scan/{scan_id}/results")
def api_results(scan_id: str):
    session = scan_sessions.get_session(scan_id)
    if session is None:
        return _err("SCAN_NOT_FOUND", "scan", scan_id)
    if session.result is not None:
        return session.result
    return session.partial_result()


@app.post("/api/scan/{scan_id}/stop")
def api_stop(scan_id: str):
    session = scan_sessions.stop_scan(scan_id)
    if session is None:
        return _err("SCAN_NOT_FOUND", "scan", scan_id)
    return {"scan_id": scan_id, "status": session.status}


@app.get("/api/scan/current")
def api_current():
    session = scan_sessions.current_session()
    if session is None:
        return _err("SCAN_NOT_FOUND", "scan", "-")
    return {"scan_id": session.scan_id, "status": session.status,
            "tables": list(session.selected)}


@app.get("/api/scan/last")
def api_last():
    try:
        from .storage import load_last_scan
        result = load_last_scan()
    except Exception as e:
        get_logger().error("last scan unreadable: %s: %s", type(e).__name__, e,
                           extra={"scan_id": "-", "step": "save", "table": "-"})
        return _err("STORAGE_FAILED", "save", "-")
    if result is None:
        return _err("SCAN_NOT_FOUND", "save", "-")
    return result


_FRONTEND = Path(__file__).resolve().parent.parent.parent / "frontend"
if (_FRONTEND / "index.html").exists():
    app.mount("/", StaticFiles(directory=str(_FRONTEND), html=True),
              name="frontend")
else:
    @app.get("/")
    def _root():
        return {"service": "db-norm-tool api", "ui": "not built yet (step 10)"}


setup_logging()
