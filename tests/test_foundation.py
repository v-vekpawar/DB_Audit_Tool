"""Step 0 checkpoint: pytest runs; log filter masks secrets; error object shape."""

import logging

from app.errors import make_error
from app.logging_setup import SecretMaskingFilter, set_secrets


def test_placeholder():
    assert True


def test_log_filter_masks_password(caplog):
    set_secrets(["s3cr3t-pw"])
    try:
        rec = logging.LogRecord("dbnorm", logging.INFO, __file__, 1, "connect with s3cr3t-pw", (), None)
        f = SecretMaskingFilter()
        assert f.filter(rec)
        assert "s3cr3t-pw" not in rec.getMessage()
        assert "***" in rec.getMessage()
    finally:
        set_secrets([])


def test_error_object_shape():
    e = make_error("DB_AUTH_FAILED", step="connect", scan_id="abc")["error"]
    assert e["code"] == "DB_AUTH_FAILED"
    assert e["message"] and e["hint"] and e["step"] == "connect" and e["scan_id"] == "abc"
