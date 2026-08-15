import datetime as dt
import json
from pathlib import Path

import pytest

from ptb.core import warehouse
from ptb.core.silver import fpl as loader

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def con(tmp_path):
    connection = warehouse.connect(tmp_path / "test.duckdb")
    yield connection
    connection.close()


def _load(con, name):
    payload = json.loads((FIX / name).read_text(encoding="utf-8"))
    return loader.load(con, payload, "fpl/" + name)


def test_fpl_is_registered():
    from ptb.core.silver import SOURCES
    assert "fpl" in SOURCES


def test_bootstrap_loads_teams_positions_events_elements(con):
    _load(con, "fpl_bootstrap_sample.json")
    assert con.execute("SELECT count(*) FROM src_fpl_team").fetchone()[0] == 2
    assert con.execute("SELECT count(*) FROM src_fpl_position").fetchone()[0] == 2
    assert con.execute("SELECT count(*) FROM src_fpl_event").fetchone()[0] == 1
    assert con.execute("SELECT count(*) FROM src_fpl_element").fetchone()[0] == 1


def test_bootstrap_maps_team_and_element_columns(con):
    _load(con, "fpl_bootstrap_sample.json")
    team = con.execute(
        "SELECT name, short_name, strength_overall_home FROM src_fpl_team "
        "WHERE season = '2024/25' AND team_id = 1"
    ).fetchone()
    assert team == ("Arsenal", "ARS", 1300)

    el = con.execute(
        "SELECT web_name, team, element_type, now_cost, total_points, "
        "       selected_by_percent, expected_goals, code "
        "FROM src_fpl_element WHERE season = '2024/25' AND element_id = 11"
    ).fetchone()
    assert el[0] == "Saka"
    assert (el[1], el[2], el[3], el[4]) == (1, 3, 100, 200)
    assert el[5] == pytest.approx(40.2)
    assert el[6] == pytest.approx(10.5)
    assert el[7] == 223094


def test_bootstrap_is_idempotent(con):
    _load(con, "fpl_bootstrap_sample.json")
    _load(con, "fpl_bootstrap_sample.json")
    assert con.execute("SELECT count(*) FROM src_fpl_team").fetchone()[0] == 2
    assert con.execute("SELECT count(*) FROM src_fpl_element").fetchone()[0] == 1


def test_fixtures_loader_writes_rows(con):
    assert _load(con, "fpl_fixtures_sample.json") == 2
    assert con.execute("SELECT count(*) FROM src_fpl_fixture").fetchone()[0] == 2


def test_fixtures_loader_maps_columns(con):
    _load(con, "fpl_fixtures_sample.json")
    row = con.execute(
        "SELECT event, kickoff_time, team_h, team_a, team_h_score, team_a_score, "
        "       finished, team_h_difficulty FROM src_fpl_fixture "
        "WHERE season = '2024/25' AND fixture_id = 1"
    ).fetchone()
    assert row[0] == 1
    assert row[1] == dt.datetime(2024, 8, 17, 14, 0)
    assert (row[2], row[3]) == (1, 20)
    assert (row[4], row[5]) == (2, 0)
    assert row[6] is True
    assert row[7] == 2


def test_fixtures_loader_is_idempotent(con):
    _load(con, "fpl_fixtures_sample.json")
    _load(con, "fpl_fixtures_sample.json")
    assert con.execute("SELECT count(*) FROM src_fpl_fixture").fetchone()[0] == 2


def test_element_history_loads_each_gameweek(con):
    assert _load(con, "fpl_element_sample.json") == 2
    assert con.execute(
        "SELECT count(*) FROM src_fpl_player_gw WHERE source = 'api'"
    ).fetchone()[0] == 2


def test_element_history_maps_columns(con):
    _load(con, "fpl_element_sample.json")
    row = con.execute(
        "SELECT event, fixture, opponent_team, minutes, total_points, goals_scored, "
        "       assists, bonus, bps, expected_goals, value, was_home "
        "FROM src_fpl_player_gw WHERE element_id = 11 AND event = 1 AND source = 'api'"
    ).fetchone()
    assert row[0] == 1
    assert (row[1], row[2]) == (1, 20)
    assert (row[3], row[4]) == (90, 13)
    assert (row[5], row[6]) == (1, 1)
    assert (row[7], row[8]) == (2, 45)
    assert row[9] == pytest.approx(0.55)
    assert row[10] == 100
    assert row[11] is True


def test_element_history_is_idempotent(con):
    _load(con, "fpl_element_sample.json")
    _load(con, "fpl_element_sample.json")
    assert con.execute("SELECT count(*) FROM src_fpl_player_gw").fetchone()[0] == 2


def test_bootstrap_loads_birth_date_and_opta_code(con):
    """Both are identity signal for cross-source player matching, and both are
    in every archived bootstrap payload."""
    _load(con, "fpl_bootstrap_sample.json")
    row = con.execute(
        "SELECT birth_date, opta_code FROM src_fpl_element "
        "WHERE season = '2024/25' AND element_id = 11"
    ).fetchone()
    assert row == (dt.date(2001, 9, 5), "p223094")


def test_bootstrap_tolerates_missing_birth_date(con):
    """FPL only began publishing these recently, so historical payloads lack
    them and must still load."""
    payload = json.loads(
        (FIX / "fpl_bootstrap_sample.json").read_text(encoding="utf-8"))
    for element in payload["data"]["elements"]:
        element.pop("birth_date", None)
        element.pop("opta_code", None)
    loader.load(con, payload, "fpl/no-birth-date")
    row = con.execute(
        "SELECT birth_date, opta_code FROM src_fpl_element "
        "WHERE season = '2024/25' AND element_id = 11"
    ).fetchone()
    assert row == (None, None)
