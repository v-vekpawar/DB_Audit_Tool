"""Storage: last-scan round-trip, incl. non-JSON-native values (dates)."""

import datetime

from app import storage


def test_save_load_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "last.sqlite")
    result = {"scan": {"id": "s1", "status": "completed",
                       "counts": {"faulted": 1}},
              "table_summary": [], "tables": [], "notices": []}
    storage.save_last_scan(result, scan_id="s1")
    assert storage.load_last_scan() == result


def test_save_converts_dates(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "last.sqlite")
    result = {"scan": {"id": "s1"}, "when": datetime.date(2026, 10, 4),
              "tables": []}
    storage.save_last_scan(result, scan_id="s1")
    loaded = storage.load_last_scan()
    assert loaded["when"] == "2026-10-04"


def test_load_empty_is_none(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "last.sqlite")
    assert storage.load_last_scan() is None
