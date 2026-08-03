import datetime as dt

import pytest

from ptb.core.archive import LocalBackend, RawArchive
from ptb.core.sources import understat as us


def test_season_label_and_parse_roundtrip():
    for year in (2014, 2019, 2024):
        assert us.parse_season(us.season_label(year)) == year
    assert us.season_label(2024) == "2024/25"
    assert us.season_label(2019) == "2019/20"


def test_leagues_map_to_plan1_competitions():
    assert us.LEAGUE_TO_COMPETITION["EPL"] == "E0"
    assert set(us.LEAGUE_TO_COMPETITION) == set(us.LEAGUES)


def _league_payload():
    return {
        "teams": {}, "players": [],
        "dates": [
            {"id": "1001", "isResult": True,
             "h": {"id": "1", "title": "Arsenal", "short_title": "ARS"},
             "a": {"id": "2", "title": "Wolverhampton Wanderers", "short_title": "WOL"},
             "goals": {"h": "2", "a": "0"}, "xG": {"h": "1.9", "a": "0.4"},
             "datetime": "2024-08-17 15:00:00",
             "forecast": {"w": "0.7", "d": "0.2", "l": "0.1"}},
            {"id": "1002", "isResult": False,
             "h": {"id": "1", "title": "Arsenal", "short_title": "ARS"},
             "a": {"id": "3", "title": "Chelsea", "short_title": "CHE"},
             "goals": {"h": "0", "a": "0"}, "xG": {"h": "0", "a": "0"},
             "datetime": "2025-05-01 15:00:00",
             "forecast": {"w": "0.4", "d": "0.3", "l": "0.3"}},
        ],
    }


def test_ingest_archives_league_then_played_matches(tmp_path, monkeypatch):
    archive = RawArchive(LocalBackend(tmp_path))
    monkeypatch.setattr(us, "_fetch_league", lambda s, lg, yr: _league_payload())
    monkeypatch.setattr(us, "_fetch_match", lambda s, mid: {"shots": {"h": [], "a": []}})

    keys = us.UnderstatSource().ingest(
        archive, leagues=["EPL"], seasons=[2024],
        captured_at=dt.datetime(2026, 8, 3, 12, 0, 0),
    )

    # one league payload + one match payload (only isResult == True is fetched)
    assert any(k.startswith("understat/league/EPL__2024-25__") for k in keys)
    assert any(k.startswith("understat/match/1001__") for k in keys)
    assert not any("/match/1002__" in k for k in keys)

    league_key = next(k for k in keys if "/league/" in k)
    env = archive.read(league_key)
    assert env["competition"] == "E0"
    assert env["season"] == "2024/25"
    assert env["data"]["dates"][0]["id"] == "1001"


def test_ingest_is_incremental_by_default(tmp_path, monkeypatch):
    archive = RawArchive(LocalBackend(tmp_path))
    monkeypatch.setattr(us, "_fetch_league", lambda s, lg, yr: _league_payload())

    calls = []
    monkeypatch.setattr(us, "_fetch_match",
                        lambda s, mid: calls.append(mid) or {"shots": {"h": [], "a": []}})

    us.UnderstatSource().ingest(archive, leagues=["EPL"], seasons=[2024],
                                captured_at=dt.datetime(2026, 8, 3, 12, 0, 0))
    us.UnderstatSource().ingest(archive, leagues=["EPL"], seasons=[2024],
                                captured_at=dt.datetime(2026, 8, 3, 13, 0, 0))

    assert calls == ["1001"]  # second run skipped the already-archived match


def test_refetch_forces_match_refetch(tmp_path, monkeypatch):
    archive = RawArchive(LocalBackend(tmp_path))
    monkeypatch.setattr(us, "_fetch_league", lambda s, lg, yr: _league_payload())
    calls = []
    monkeypatch.setattr(us, "_fetch_match",
                        lambda s, mid: calls.append(mid) or {"shots": {"h": [], "a": []}})

    us.UnderstatSource().ingest(archive, leagues=["EPL"], seasons=[2024],
                                captured_at=dt.datetime(2026, 8, 3, 12, 0, 0))
    us.UnderstatSource().ingest(archive, leagues=["EPL"], seasons=[2024], refetch=True,
                                captured_at=dt.datetime(2026, 8, 3, 13, 0, 0))

    assert calls == ["1001", "1001"]


def test_ingest_rejects_unknown_league(tmp_path):
    archive = RawArchive(LocalBackend(tmp_path))
    with pytest.raises(ValueError, match="Bundesliga2"):
        us.UnderstatSource().ingest(archive, leagues=["Bundesliga2"], seasons=[2024])
