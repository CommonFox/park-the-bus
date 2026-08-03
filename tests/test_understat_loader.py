import datetime as dt
import json
from pathlib import Path

import pytest

from ptb.core.warehouse import db
from ptb.core.warehouse.loaders import understat as loader

LEAGUE_FIXTURE = Path(__file__).parent / "fixtures" / "understat_league_epl_2024_sample.json"


@pytest.fixture
def con(tmp_path):
    connection = db.connect(tmp_path / "test.duckdb")
    yield connection
    connection.close()


@pytest.fixture
def league_payload():
    return json.loads(LEAGUE_FIXTURE.read_text(encoding="utf-8"))


def test_league_loader_writes_only_played_matches(con, league_payload):
    # The fixture has one played match (1001, isResult true) and one unplayed
    # fixture (1002, isResult false, 0-0 with no xG). Only the played one is stored.
    rows = loader.load_understat(con, league_payload, "understat/league/k.json.gz")
    assert rows == 1
    assert con.execute("SELECT count(*) FROM src_understat_match").fetchone()[0] == 1
    assert con.execute(
        "SELECT count(*) FROM src_understat_match WHERE understat_match_id = '1002'"
    ).fetchone()[0] == 0


def test_league_loader_maps_columns(con, league_payload):
    loader.load_understat(con, league_payload, "understat/league/k.json.gz")
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


def test_league_loader_is_idempotent(con, league_payload):
    loader.load_understat(con, league_payload, "k")
    loader.load_understat(con, league_payload, "k")
    assert con.execute("SELECT count(*) FROM src_understat_match").fetchone()[0] == 1


def test_understat_is_registered():
    from ptb.core.warehouse.load import LOADERS
    assert "understat" in LOADERS
