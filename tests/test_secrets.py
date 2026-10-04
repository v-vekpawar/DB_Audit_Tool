"""Step 17: no secrets or row values ever reach logs; no password ever
reaches API errors or stored results."""

import json
import logging

import pytest
from sqlalchemy import create_engine, text

from app.analyzer import analyze_on_connection
from app.db_connect import ConnectParams
from app.logging_setup import SecretMaskingFilter, get_logger, set_secrets

PASSWORD = "S3cr3t-pw-xyz-999"
MARKER = "SecretValue-UNIQUE-123"


class Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


@pytest.fixture()
def captured():
    logger = get_logger()
    handler = Capture()
    handler.addFilter(SecretMaskingFilter())
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        yield handler
    finally:
        logger.removeHandler(handler)
        set_secrets([])


@pytest.fixture()
def sqlite_conn():
    e = create_engine("sqlite://")
    with e.begin() as c:
        c.execute(text("CREATE TABLE t (id INTEGER PRIMARY KEY, v VARCHAR(40))"))
        for i in range(30):
            c.execute(text("INSERT INTO t VALUES (:i, :v)"),
                      {"i": i, "v": "%s-%d" % (MARKER, i % 3)})
    conn = e.connect()
    yield e, conn
    conn.close()


def test_password_masked_in_log_output(captured):
    set_secrets([PASSWORD])
    try:
        get_logger().info("connecting with %s user", PASSWORD,
                           extra={"scan_id": "s", "step": "connect", "table": "-"})
    finally:
        set_secrets([])
    assert PASSWORD not in " ".join(captured.lines)
    assert "***" in " ".join(captured.lines)


def test_scan_logs_have_no_password_or_values(captured, sqlite_conn):
    e, conn = sqlite_conn
    params = ConnectParams(db_type="mysql", host="h", port=3306,
                           username="u", password=PASSWORD, database="x")
    out = analyze_on_connection(e, conn, params, ["t"], scan_id="leak")
    blob = " ".join(captured.lines)
    assert PASSWORD not in blob
    assert MARKER not in blob
    assert PASSWORD not in json.dumps(out)
    # evidence examples intentionally keep real values (shown on cards);
    # passwords must never be stored anywhere.
    assert out["scan"]["status"] == "completed"


def test_error_objects_carry_no_password():
    from app.errors import make_error
    err = make_error("DB_AUTH_FAILED", step="connect", scan_id="s")["error"]
    assert PASSWORD not in json.dumps(err)
