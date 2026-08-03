import datetime as dt

import pytest

from ptb.core.archive import LocalBackend, RawArchive
from ptb.core.sources import fpl


def test_season_from_events_uses_first_deadline_year():
    events = [{"id": 1, "deadline_time": "2024-08-16T17:30:00Z"},
              {"id": 38, "deadline_time": "2025-05-25T14:00:00Z"}]
    assert fpl.season_from_events(events) == "2024/25"


def _bootstrap():
    return {
        "events": [{"id": 1, "deadline_time": "2024-08-16T17:30:00Z"}],
        "teams": [{"id": 1, "name": "Arsenal"}],
        "element_types": [{"id": 1, "singular_name_short": "GKP"}],
        "elements": [{"id": 11, "web_name": "Raya", "team": 1},
                     {"id": 12, "web_name": "Saka", "team": 1}],
    }


def test_ingest_archives_bootstrap_fixtures_and_each_element(tmp_path, monkeypatch):
    archive = RawArchive(LocalBackend(tmp_path))
    monkeypatch.setattr(fpl, "_fetch_bootstrap", lambda s: _bootstrap())
    monkeypatch.setattr(fpl, "_fetch_fixtures", lambda s: [{"id": 1, "event": 1}])
    monkeypatch.setattr(fpl, "_fetch_element", lambda s, eid: {"history": [], "element": eid})

    keys = fpl.FplSource().ingest(archive, captured_at=dt.datetime(2026, 8, 3, 12, 0, 0))

    assert any(k.startswith("fpl/bootstrap/2024-25__") for k in keys)
    assert any(k.startswith("fpl/fixtures/2024-25__") for k in keys)
    assert any(k.startswith("fpl/element/11__") for k in keys)
    assert any(k.startswith("fpl/element/12__") for k in keys)

    boot = archive.read(next(k for k in keys if "/bootstrap/" in k))
    assert boot["season"] == "2024/25"
    assert boot["data"]["teams"][0]["name"] == "Arsenal"

    el = archive.read(next(k for k in keys if "/element/11__" in k))
    assert el["element_id"] == 11
    assert el["season"] == "2024/25"


def test_ingest_always_refetches_elements(tmp_path, monkeypatch):
    """FPL is live current-season data: every run captures a fresh snapshot,
    so elements are fetched even when the archive already has them."""
    archive = RawArchive(LocalBackend(tmp_path))
    monkeypatch.setattr(fpl, "_fetch_bootstrap", lambda s: _bootstrap())
    monkeypatch.setattr(fpl, "_fetch_fixtures", lambda s: [])
    calls = []
    monkeypatch.setattr(fpl, "_fetch_element",
                        lambda s, eid: calls.append(eid) or {"history": []})

    fpl.FplSource().ingest(archive, captured_at=dt.datetime(2026, 8, 3, 12, 0, 0))
    fpl.FplSource().ingest(archive, captured_at=dt.datetime(2026, 8, 3, 13, 0, 0))

    assert calls == [11, 12, 11, 12]  # both players fetched on both runs
