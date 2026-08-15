import datetime as dt

from ptb.core import archive
from ptb.core.silver import draftkings as dk


def _payload(subcategory):
    # a minimal well-formed subcategory response
    return {"events": [{"id": "E1", "name": "Arsenal vs Wolves",
                        "startEventDate": "2026-08-21T19:00:00.0000000Z",
                        "participants": [{"name": "Arsenal", "venueRole": "Home"},
                                         {"name": "Wolves", "venueRole": "Away"}]}],
            "markets": [{"id": "M1", "eventId": "E1"}],
            "selections": [{"marketId": "M1", "outcomeType": "Home",
                            "displayOdds": {"decimal": "1.5"}}],
            "subcategory": subcategory}


def test_ingest_archives_one_combined_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(dk, "_fetch_subcategory",
                        lambda s, sub, league, site: _payload(sub))

    keys = dk.ingest(captured_at=dt.datetime(2026, 8, 3, 12, 0, 0))

    assert len(keys) == 1
    assert keys[0].startswith("draftkings/markets/")
    env = archive.read(keys[0])
    assert env["league"] == "premier-league"
    assert env["captured_at"] == "2026-08-03T12:00:00Z"
    assert env["data"]["moneyline"]["subcategory"] == dk.SUBCATEGORY_MONEYLINE
    assert env["data"]["totals"]["subcategory"] == dk.SUBCATEGORY_TOTALS


def test_ingest_returns_empty_when_blocked(tmp_path, monkeypatch):

    def blocked(session, subcategory, league, site):
        raise dk.Blocked("fingerprint gate changed")

    monkeypatch.setattr(dk, "_fetch_subcategory", blocked)

    keys = dk.ingest(captured_at=dt.datetime(2026, 8, 3, 12, 0, 0))
    assert keys == []
    assert archive.keys(source="draftkings") == []


def test_ingest_snapshots_are_append_only(tmp_path, monkeypatch):
    monkeypatch.setattr(dk, "_fetch_subcategory",
                        lambda s, sub, league, site: _payload(sub))

    dk.ingest(captured_at=dt.datetime(2026, 8, 3, 12, 0, 0))
    dk.ingest(captured_at=dt.datetime(2026, 8, 3, 13, 0, 0))
    assert len(archive.keys(source="draftkings")) == 2
