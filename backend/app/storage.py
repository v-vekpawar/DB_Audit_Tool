"""Single-row SQLite store for the last scan result (never credentials)."""

import json
import sqlite3
from pathlib import Path

from . import config
from .logging_setup import get_logger

DB_PATH = Path(config.LAST_SCAN_DB)

_SCHEMA = """CREATE TABLE IF NOT EXISTS last_scan (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  saved_at TEXT NOT NULL,
  result_json TEXT NOT NULL
)"""


def _connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(_SCHEMA)
    return conn


def save_last_scan(result: dict, scan_id: str = "-") -> None:
    """Overwrite the single stored row. Raises on failure (caller logs)."""
    from datetime import datetime, timezone
    conn = _connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO last_scan (id, saved_at, result_json) VALUES (1, ?, ?)",
            (datetime.now(timezone.utc).isoformat(),
             json.dumps(result, default=str)),
        )
        conn.commit()
    finally:
        conn.close()
    get_logger().info("last scan saved", extra={"scan_id": scan_id, "step": "save", "table": "-"})


def load_last_scan() -> dict | None:
    conn = _connect()
    try:
        row = conn.execute("SELECT result_json FROM last_scan WHERE id = 1").fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return json.loads(row[0])
