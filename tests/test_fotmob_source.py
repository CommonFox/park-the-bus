import datetime as dt

import pytest

from ptb.core.archive import LocalBackend, RawArchive
from ptb.core.sources import fotmob as fm


def test_season_label_shortens_fotmob_name():
    assert fm.season_label("2024/2025") == "2024/25"
    assert fm.season_label("2016/2017") == "2016/17"


def test_leagues_are_premier_league_and_championship():
    assert fm.LEAGUES == {"premier-league": 47, "championship": 48}


# A canned API response for one (season, stat, type).
def _response(stat, stat_type, names):
    return {
        "seasons": [{"id": 23685, "name": "2024/2025", "leagueId": 47},
                    {"id": 27110, "name": "2025/2026", "leagueId": 47}],
        "statsList": [{"name": n, "title": n.title(), "category": "Attacking"} for n in names],
        "leagueDetails": {"id": 47, "name": "Premier League"},
        "statsData": [{"id": 1, "teamId": 2, "name": "Player", "position": 83,
                       "statValue": {"name": stat, "value": 1.0}, "substatValue": {"value": 2},
                       "rank": 1, "type": stat_type}],
        "type": stat_type,
    }


def _patch(monkeypatch, calls):
    monkeypatch.setattr(fm, "_seasons",
                        lambda s, lid: [{"id": 23685, "name": "2024/2025", "leagueId": lid}])
    monkeypatch.setattr(fm, "_stats_list",
                        lambda s, lid, sid, t: ["goals"] if t == "players" else ["rating_team"])

    def fake_fetch(session, league_id, season_id, stat, stat_type):
        calls.append((league_id, season_id, stat, stat_type))
        return _response(stat, stat_type, ["goals"] if stat_type == "players" else ["rating_team"])

    monkeypatch.setattr(fm, "_fetch_stat", fake_fetch)


def test_ingest_sweeps_each_stat_and_type(tmp_path, monkeypatch):
    archive = RawArchive(LocalBackend(tmp_path))
    calls = []
    _patch(monkeypatch, calls)

    keys = fm.FotmobSource().ingest(
        archive, leagues=["premier-league"], seasons=["2024/25"],
        captured_at=dt.datetime(2026, 8, 3, 12, 0, 0))

    assert any(k.startswith("fotmob/premier-league/players/2024-25__goals__") for k in keys)
    assert any(k.startswith("fotmob/premier-league/teams/2024-25__rating_team__") for k in keys)

    env = archive.read(next(k for k in keys if "/players/2024-25__goals__" in k))
    assert env["league_id"] == 47
    assert env["season"] == "2024/25"
    assert env["season_id"] == 23685
    assert env["stat"] == "goals"
    assert env["stat_type"] == "players"
    assert env["data"]["statsData"][0]["name"] == "Player"


def test_ingest_is_incremental(tmp_path, monkeypatch):
    archive = RawArchive(LocalBackend(tmp_path))
    calls = []
    _patch(monkeypatch, calls)

    fm.FotmobSource().ingest(archive, leagues=["premier-league"], seasons=["2024/25"],
                             captured_at=dt.datetime(2026, 8, 3, 12, 0, 0))
    first = len(calls)
    fm.FotmobSource().ingest(archive, leagues=["premier-league"], seasons=["2024/25"],
                             captured_at=dt.datetime(2026, 8, 3, 13, 0, 0))
    assert len(calls) == first  # everything already archived


def test_refetch_repulls(tmp_path, monkeypatch):
    archive = RawArchive(LocalBackend(tmp_path))
    calls = []
    _patch(monkeypatch, calls)

    fm.FotmobSource().ingest(archive, leagues=["premier-league"], seasons=["2024/25"],
                             captured_at=dt.datetime(2026, 8, 3, 12, 0, 0))
    n = len(calls)
    fm.FotmobSource().ingest(archive, leagues=["premier-league"], seasons=["2024/25"],
                             refetch=True, captured_at=dt.datetime(2026, 8, 3, 13, 0, 0))
    assert len(calls) > n


def test_ingest_rejects_unknown_league(tmp_path):
    archive = RawArchive(LocalBackend(tmp_path))
    with pytest.raises(ValueError, match="la-liga"):
        fm.FotmobSource().ingest(archive, leagues=["la-liga"], seasons=["2024/25"])
