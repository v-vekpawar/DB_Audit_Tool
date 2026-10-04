"""Rotating file + console logging with secret masking.

Never logs passwords, full connection URLs, or row values.
Logs hold names, counts and error codes only.
"""

import logging
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import config

# Populated per scan via set_secrets(); the filter masks any occurrence.
_SECRETS: list[str] = []


def set_secrets(secrets: list[str]) -> None:
    global _SECRETS
    _SECRETS = [s for s in secrets if s]


class SecretMaskingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for s in _SECRETS:
            if s and s in msg:
                record.msg = msg.replace(s, "***")
                record.args = ()
                msg = record.getMessage()
        return True


_configured = False


def setup_logging() -> logging.Logger:
    global _configured
    logger = logging.getLogger("dbnorm")
    logger.setLevel(getattr(logging, config.LOG_LEVEL, logging.INFO))
    if _configured:
        return logger
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(scan_id)s | %(step)s | %(table)s | %(message)s")
    # Defaults so format never fails when extras are missing.
    filt = SecretMaskingFilter()
    logger.addFilter(DefaultContextFilter())

    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    ch.addFilter(filt)
    logger.addHandler(ch)

    log_path = Path(config.LOG_FILE)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fh = RotatingFileHandler(
        log_path,
        maxBytes=config.LOG_MAX_BYTES,
        backupCount=config.LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    fh.setFormatter(fmt)
    fh.addFilter(filt)
    logger.addHandler(fh)

    logger.addFilter(filt)
    _configured = True
    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger("dbnorm")


class StepTimer:
    """Helper: logs a step's start, outcome and duration."""

    def __init__(self, step: str, scan_id: str = "-", table: str = "-"):
        self.step = step
        self.scan_id = scan_id
        self.table = table
        self._t0 = 0.0

    def __enter__(self) -> "StepTimer":
        self._t0 = time.monotonic()
        get_logger().info(
            "started", extra={"scan_id": self.scan_id, "step": self.step, "table": self.table}
        )
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        dt = time.monotonic() - self._t0
        if exc_type is None:
            get_logger().info(
                "done (%.2fs)", dt,
                extra={"scan_id": self.scan_id, "step": self.step, "table": self.table},
            )
        else:
            get_logger().error(
                "failed: %s (%.2fs)", exc, dt,
                extra={"scan_id": self.scan_id, "step": self.step, "table": self.table},
            )

    def ok(self, detail: str = "ok") -> float:
        dt = time.monotonic() - self._t0
        get_logger().info(
            "%s (%.2fs)", detail, dt,
            extra={"scan_id": self.scan_id, "step": self.step, "table": self.table},
        )
        return dt

    def fail(self, detail: str) -> float:
        dt = time.monotonic() - self._t0
        get_logger().error(
            "%s (%.2fs)", detail, dt,
            extra={"scan_id": self.scan_id, "step": self.step, "table": self.table},
        )
        return dt


class DefaultContextFilter(logging.Filter):
    """Fills scan_id/step/table defaults when a call site omits them."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "scan_id"):
            record.scan_id = "-"
        if not hasattr(record, "step"):
            record.step = "-"
        if not hasattr(record, "table"):
            record.table = "-"
        return True
