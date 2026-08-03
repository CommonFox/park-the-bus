import datetime as dt
import json
from pathlib import Path

import pytest

from ptb.core.warehouse import db
from ptb.core.warehouse.loaders import asa as loader

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def con(tmp_path):
    connection = db.connect(tmp_path / "test.duckdb")
    yield connection
    connection.close()


def _load(con, name):
    payload = json.loads((FIX / name).read_text(encoding="utf-8"))
    return loader.load_asa(con, payload, "asa/" + name)


def test_asa_is_registered():
    from ptb.core.warehouse.load import LOADERS
    assert "asa" in LOADERS


def test_teams_loader(con):
    assert _load(con, "asa_nwsl_teams_sample.json") == 2
    row = con.execute(
        "SELECT team_name, team_abbreviation FROM src_asa_team "
        "WHERE league = 'nwsl' AND team_id = 'T_POR'"
    ).fetchone()
    assert row == ("Portland Thorns FC", "POR")


def test_players_loader_one_row_per_season(con):
    assert _load(con, "asa_nwsl_players_sample.json") == 2
    row = con.execute(
        "SELECT player_name, birth_date FROM src_asa_player "
        "WHERE player_id = 'P1' AND season = '2024'"
    ).fetchone()
    assert row == ("Sophia Smith", dt.date(2000, 8, 10))


def test_games_loader_parses_utc_kickoff(con):
    assert _load(con, "asa_nwsl_games_sample.json") == 1
    row = con.execute(
        "SELECT league, season, kickoff_utc, home_team_id, away_team_id, "
        "       home_score, away_score, status FROM src_asa_game WHERE game_id = 'G1'"
    ).fetchone()
    assert row[0] == "nwsl"
    assert row[1] == "2024"
    assert row[2] == dt.datetime(2024, 6, 15, 2, 30)
    assert (row[3], row[4]) == ("T_POR", "T_SEA")
    assert (row[5], row[6]) == (2, 1)
    assert row[7] == "FullTime"


def test_game_xgoals_loader(con):
    assert _load(con, "asa_nwsl_games_xgoals_sample.json") == 1
    row = con.execute(
        "SELECT home_team_xgoals, away_team_xgoals, home_xpoints "
        "FROM src_asa_game_xgoals WHERE game_id = 'G1'"
    ).fetchone()
    assert row[0] == pytest.approx(1.8)
    assert row[1] == pytest.approx(0.9)
    assert row[2] == pytest.approx(2.1)


def test_loaders_are_idempotent(con):
    for name in ("asa_nwsl_teams_sample.json", "asa_nwsl_games_sample.json"):
        _load(con, name)
        _load(con, name)
    assert con.execute("SELECT count(*) FROM src_asa_team").fetchone()[0] == 2
    assert con.execute("SELECT count(*) FROM src_asa_game").fetchone()[0] == 1


def test_goals_added_explodes_by_action_type(con):
    assert _load(con, "asa_nwsl_goals_added_sample.json") == 2
    rows = con.execute(
        "SELECT action_type, goals_added_raw, count_actions "
        "FROM src_asa_player_goals_added WHERE player_id = 'P1' ORDER BY action_type"
    ).fetchall()
    assert rows == [("Receiving", pytest.approx(0.8), 400),
                    ("Shooting", pytest.approx(2.5), 60)]


def test_player_xgoals_loader(con):
    assert _load(con, "asa_nwsl_xgoals_sample.json") == 1
    row = con.execute(
        "SELECT goals, xgoals, primary_assists, points_added "
        "FROM src_asa_player_xgoals WHERE player_id = 'P1' AND season = '2024'"
    ).fetchone()
    assert row[0] == 14
    assert row[1] == pytest.approx(12.6)
    assert row[2] == 5
    assert row[3] == pytest.approx(3.4)


def test_player_xpass_loader(con):
    assert _load(con, "asa_nwsl_xpass_sample.json") == 1
    row = con.execute(
        "SELECT attempted_passes, xpass_completion_percentage, count_games "
        "FROM src_asa_player_xpass WHERE player_id = 'P1' AND season = '2024'"
    ).fetchone()
    assert row[0] == 900
    assert row[1] == pytest.approx(0.74)
    assert row[2] == 22


def test_player_stat_loaders_are_idempotent(con):
    for name in ("asa_nwsl_goals_added_sample.json", "asa_nwsl_xgoals_sample.json"):
        _load(con, name)
        _load(con, name)
    assert con.execute("SELECT count(*) FROM src_asa_player_goals_added").fetchone()[0] == 2
    assert con.execute("SELECT count(*) FROM src_asa_player_xgoals").fetchone()[0] == 1
