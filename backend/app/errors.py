"""Error codes, plain messages, hints, structured error object.

Catalogue from docs/DESIGN.md section 13. Raw driver text and stack
traces go to the log only, never to the browser.
"""

ERRORS: dict[str, tuple[str, str, int]] = {
    # code: (message, hint, http_status)
    "VALIDATION_ERROR": ("Invalid input: {detail}", "Check the highlighted field and try again.", 400),
    "DB_DRIVER_MISSING": ("Database driver is not installed in this environment.", "Reinstall from the wheels folder on vm1.", 500),
    "DB_UNREACHABLE": ("Database connection not established: host unreachable or port closed.", "Check the IP/port with Test-NetConnection and that the DB allows remote connections from vm1.", 502),
    "DB_TIMEOUT": ("Database connection not established: connection timed out.", "Check firewall rules; the network may be dropping packets.", 502),
    "DB_AUTH_FAILED": ("Database connection not established: the username or password was rejected.", "Check the credentials and that this user may connect from vm1.", 502),
    "DB_NOT_FOUND": ("Connected to the server, but the database / service name was not found.", "Check the database name (Oracle: use the service name, e.g. FREEPDB1, not the SID).", 502),
    "DB_SSL_FAILED": ("SSL connection could not be established.", "Check whether the server offers SSL.", 502),
    "DB_READONLY_FAILED": ("Could not put the session in read-only mode; scan not started.", "Check engine permissions. The tool refuses to scan without read-only mode.", 502),
    "NO_TABLES": ("Connected, but no tables were found in this database / schema.", "Check the schema name and that the user has privileges.", 502),
    "TABLE_PERMISSION_DENIED": ("Table skipped: this user cannot read it.", "Grant SELECT on this table.", 502),
    "TABLE_TIMEOUT": ("Table skipped: query exceeded the time limit.", "The table may be very large or locked.", 502),
    "TABLE_NOT_FOUND": ("Table skipped: it no longer exists in the database.", "It was dropped or renamed between Connect and Scan.", 502),
    "TABLE_ANALYSIS_FAILED": ("Table skipped: analysis failed unexpectedly.", "See the log under this scan id and table name.", 500),
    "SCAN_ALREADY_RUNNING": ("A scan is already in progress.", "Wait for it to finish or stop it first.", 409),
    "SCAN_NOT_FOUND": ("No such scan.", "The link may be old or the backend was restarted.", 404),
    "STORAGE_FAILED": ("Result shown, but could not be saved as the last scan.", "Check disk space and permissions on the SQLite file.", 500),
    "EXPORT_FAILED": ("PDF could not be generated.", "See the log under this scan id.", 500),
    "BACKEND_UNREACHABLE": ("Cannot reach the tool's backend.", "Check the backend is running on 127.0.0.1:8770.", 502),
    "PROGRESS_STREAM_LOST": ("Live progress connection was lost; the scan may still be running.", "The page will resync from partial results.", 502),
    "INTERNAL_ERROR": ("Unexpected error; reference the scan id and see the log.", "Search backend/logs/dbnorm.log for the scan id.", 500),
}


def make_error(code: str, step: str, scan_id: str, detail: str = "") -> dict:
    message, hint, _ = ERRORS.get(code, ERRORS["INTERNAL_ERROR"])
    if "{detail}" in message:
        message = message.format(detail=detail or "invalid value")
    return {"error": {"code": code, "message": message, "hint": hint, "step": step, "scan_id": scan_id}}


def http_status(code: str) -> int:
    return ERRORS.get(code, ERRORS["INTERNAL_ERROR"])[2]
