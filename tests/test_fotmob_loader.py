import json
from pathlib import Path

import pytest

from ptb.core import warehouse
from ptb.core.silver import fotmob as loader

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def con(tmp_path):
    connection = warehouse.connect(tmp_path / "test.duckdb")
    yield connection
    connection.close()


def _load(con, name):
    payload = json.loads((FIX / name).read_text(encoding="utf-8"))
    return loader.load(con, payload, "fotmob/" + name)


def test_fotmob_is_registered():
    from ptb.core.silver import SOURCES
    assert "fotmob" in SOURCES


def test_player_stat_loads_each_row(con):
    assert _load(con, "fotmob_players_xg_sample.json") == 2
    assert con.execute("SELECT count(*) FROM src_fotmob_player_stat").fetchone()[0] == 2


def test_player_stat_maps_columns_and_metadata(con):
    _load(con, "fotmob_players_xg_sample.json")
    row = con.execute(
        "SELECT league_name, season, stat_title, stat_category, fotmob_team_id, "
        "       player_name, position_code, value, substat_value, rank "
        "FROM src_fotmob_player_stat "
        "WHERE stat_name = 'expected_goals' AND fotmob_player_id = 292462"
    ).fetchone()
    assert row[0] == "Premier League"
    assert row[1] == "2024/25"
    assert row[2] == "Expected goals (xG)"      # looked up from statsList
    assert row[3] == "Attacking"
    assert row[4] == 8650
    assert row[5] == "Mohamed Salah"
    assert row[6] == 83
    assert row[7] == pytest.approx(25.4)
    assert row[8] == pytest.approx(29)
    assert row[9] == 1


def test_team_stat_loads_and_maps(con):
    assert _load(con, "fotmob_teams_xg_sample.json") == 1
    row = con.execute(
        "SELECT team_name, stat_title, value, rank FROM src_fotmob_team_stat "
        "WHERE stat_name = 'expected_goals_team' AND fotmob_team_id = 9825"
    ).fetchone()
    assert row[0] == "Arsenal"
    assert row[1] == "Expected goals (xG)"
    assert row[2] == pytest.approx(72.5)
    assert row[3] == 1


def test_loaders_are_idempotent(con):
    for name in ("fotmob_players_xg_sample.json", "fotmob_teams_xg_sample.json"):
        _load(con, name)
        _load(con, name)
    assert con.execute("SELECT count(*) FROM src_fotmob_player_stat").fetchone()[0] == 2
    assert con.execute("SELECT count(*) FROM src_fotmob_team_stat").fetchone()[0] == 1


def test_empty_statsdata_writes_nothing(con):
    payload = {
        "source": "fotmob", "league_id": 47, "season": "2024/25", "season_id": 23685,
        "stat": "goals", "stat_type": "players",
        "data": {"leagueDetails": {"name": "Premier League"}, "statsList": [], "statsData": []},
    }
    assert loader.load(con, payload, "k") == 0
