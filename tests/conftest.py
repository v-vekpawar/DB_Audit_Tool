import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import app.scan_sessions as sessions
    from app import storage
    from app.main import app
    from fastapi.testclient import TestClient

    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "last.sqlite")
    sessions._sessions.clear()
    sessions._current_id = None
    with TestClient(app) as c:
        yield c
    sessions._sessions.clear()
    sessions._current_id = None
