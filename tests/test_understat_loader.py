import datetime as dt
import json
from pathlib import Path

import pytest

from ptb.core import warehouse
from ptb.core.silver import understat as loader

LEAGUE_FIXTURE = Path(__file__).parent / "fixtures" / "understat_league_epl_2024_sample.json"


@pytest.fixture
def con(tmp_path):
    connection = warehouse.connect(tmp_path / "test.duckdb")
    yield connection
    connection.close()


@pytest.fixture
def league_payload():
    return json.loads(LEAGUE_FIXTURE.read_text(encoding="utf-8"))


def test_league_loader_writes_only_played_matches(con, league_payload):
    # The fixture has one played match (1001, isResult true) and one unplayed
    # fixture (1002, isResult false, 0-0 with no xG). Only the played one is stored,
    # alongside the fixture's two roster players.
    rows = loader.load(con, league_payload, "understat/league/k.json.gz")
    assert rows == 3
    assert con.execute("SELECT count(*) FROM src_understat_match").fetchone()[0] == 1
    assert con.execute(
        "SELECT count(*) FROM src_understat_match WHERE understat_match_id = '1002'"
    ).fetchone()[0] == 0


def test_league_loader_maps_columns(con, league_payload):
    loader.load(con, league_payload, "understat/league/k.json.gz")
    row = con.execute(
        "SELECT competition, season, kickoff, home_team, away_team, "
        "       home_goals, away_goals, home_xg, away_xg, forecast_w "
        "FROM src_understat_match WHERE understat_match_id = '1001'"
    ).fetchone()
    assert row[0] == "E0"
    assert row[1] == "2024/25"
    assert row[2] == dt.datetime(2024, 8, 17, 15, 0)
    assert (row[3], row[4]) == ("Arsenal", "Wolverhampton Wanderers")
    assert (row[5], row[6]) == (2, 0)
    assert row[7] == pytest.approx(1.94)
    assert row[8] == pytest.approx(0.41)
    assert row[9] == pytest.approx(0.71)


def test_league_loader_writes_every_roster_player_including_zero_shot_ones(con, league_payload):
    # Bernd Leno, the fixture's goalkeeper, never appears in src_understat_shot
    # (shots: "0") but must still get a roster row -- that row is what lets a
    # zero-shot player enter identity resolution at all.
    loader.load(con, league_payload, "understat/league/k.json.gz")
    assert con.execute("SELECT count(*) FROM src_understat_player").fetchone()[0] == 2
    row = con.execute(
        "SELECT player_name, team_name, position, games, minutes, shots, goals "
        "FROM src_understat_player WHERE understat_player_id = '181'"
    ).fetchone()
    assert row == ("Bernd Leno", "Fulham", "GK", 10, 900, 0, 0)


def test_league_loader_maps_player_stat_columns(con, league_payload):
    loader.load(con, league_payload, "understat/league/k.json.gz")
    row = con.execute(
        "SELECT competition, season, non_penalty_goals, assists, key_passes, "
        "       xg, npxg, xa, xg_buildup, xg_chain, yellow_cards, red_cards "
        "FROM src_understat_player WHERE understat_player_id = '500'"
    ).fetchone()
    assert (row[0], row[1]) == ("E0", "2024/25")
    assert row[2:] == (4, 3, 15, pytest.approx(4.512), pytest.approx(3.812),
                        pytest.approx(2.345), pytest.approx(1.234), pytest.approx(5.678), 1, 0)


def test_league_loader_is_idempotent(con, league_payload):
    loader.load(con, league_payload, "k")
    loader.load(con, league_payload, "k")
    assert con.execute("SELECT count(*) FROM src_understat_match").fetchone()[0] == 1
    assert con.execute("SELECT count(*) FROM src_understat_player").fetchone()[0] == 2


def test_understat_is_registered():
    from ptb.core.silver import SOURCES
    assert "understat" in SOURCES


MATCH_FIXTURE = Path(__file__).parent / "fixtures" / "understat_match_1001_sample.json"


@pytest.fixture
def match_payload():
    return json.loads(MATCH_FIXTURE.read_text(encoding="utf-8"))


def test_shot_loader_writes_all_shots_both_sides(con, match_payload):
    rows = loader.load(con, match_payload, "understat/match/1001__k.json.gz")
    assert rows == 2
    assert con.execute("SELECT count(*) FROM src_understat_shot").fetchone()[0] == 2


def test_shot_loader_maps_columns(con, match_payload):
    loader.load(con, match_payload, "k")
    row = con.execute(
        "SELECT understat_match_id, minute, player, player_id, team, home_away, "
        "       xg, result, situation, shot_type, x, y, assist_player "
        "FROM src_understat_shot WHERE understat_shot_id = '9001'"
    ).fetchone()
    assert row[0] == "1001"
    assert row[1] == 23
    assert row[2] == "Bukayo Saka"
    assert row[3] == "500"
    assert row[4] == "Arsenal"
    assert row[5] == "h"
    assert row[6] == pytest.approx(0.34)
    assert (row[7], row[8], row[9]) == ("Goal", "OpenPlay", "LeftFoot")
    assert (row[10], row[11]) == (pytest.approx(0.88), pytest.approx(0.52))
    assert row[12] == "Martin Odegaard"


def test_shot_loader_uses_correct_team_per_side(con, match_payload):
    loader.load(con, match_payload, "k")
    away = con.execute(
        "SELECT team, home_away, assist_player FROM src_understat_shot "
        "WHERE understat_shot_id = '9002'"
    ).fetchone()
    assert away == ("Wolverhampton Wanderers", "a", None)


def test_shot_loader_is_idempotent(con, match_payload):
    loader.load(con, match_payload, "k")
    loader.load(con, match_payload, "k")
    assert con.execute("SELECT count(*) FROM src_understat_shot").fetchone()[0] == 2
