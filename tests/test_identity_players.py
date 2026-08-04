import datetime as dt

import pytest

from ptb.core.identity import players
from ptb.core.warehouse import db


@pytest.fixture
def con(tmp_path):
    connection = db.connect(tmp_path / "test.duckdb")
    yield connection
    connection.close()


def _fpl_team(con, season, team_id, name):
    con.execute(
        "INSERT OR REPLACE INTO src_fpl_team (season, team_id, name, archive_key) "
        "VALUES (?, ?, ?, 'k')", [season, team_id, name])


def _fpl_element(con, season, element_id, code, first, second, team,
                 birth_date=None, opta_code=None):
    con.execute(
        "INSERT OR REPLACE INTO src_fpl_element (season, element_id, code, web_name, "
        "first_name, second_name, team, element_type, birth_date, opta_code, archive_key) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 3, ?, ?, 'k')",
        [season, element_id, code, second, first, second, team, birth_date, opta_code])


def test_spine_creates_one_player_per_fpl_code(con):
    _fpl_team(con, "2024/25", 1, "Arsenal")
    _fpl_element(con, "2024/25", 11, 223094, "Bukayo", "Saka", 1)
    assert players.build_fpl_spine(con) == 1
    assert con.execute("SELECT count(*) FROM dim_player").fetchone()[0] == 1


def test_one_player_across_two_seasons_is_one_row(con):
    """element_id is reassigned between seasons; code is not. Keying on
    element_id would make one footballer into two players."""
    _fpl_team(con, "2023/24", 1, "Arsenal")
    _fpl_team(con, "2024/25", 1, "Arsenal")
    _fpl_element(con, "2023/24", 7, 223094, "Bukayo", "Saka", 1)
    _fpl_element(con, "2024/25", 11, 223094, "Bukayo", "Saka", 1)

    assert players.build_fpl_spine(con) == 1
    row = con.execute(
        "SELECT canonical_name, fpl_code, first_seen_season, last_seen_season "
        "FROM dim_player").fetchone()
    assert row == ("Bukayo Saka", 223094, "2023/24", "2024/25")


def test_spine_maps_the_fpl_code_not_the_element_id(con):
    _fpl_team(con, "2024/25", 1, "Arsenal")
    _fpl_element(con, "2024/25", 11, 223094, "Bukayo", "Saka", 1)
    players.build_fpl_spine(con)
    row = con.execute(
        "SELECT source, source_player_id, method, confidence FROM map_player_source"
    ).fetchone()
    assert row == ("fpl", "223094", "fpl_code", 1.0)


def test_spine_carries_birth_date_and_opta_code(con):
    """Both arrive only in recent seasons, so the latest non-null wins rather
    than the latest season's value."""
    _fpl_team(con, "2023/24", 1, "Arsenal")
    _fpl_team(con, "2024/25", 1, "Arsenal")
    _fpl_element(con, "2023/24", 7, 223094, "Bukayo", "Saka", 1)
    _fpl_element(con, "2024/25", 11, 223094, "Bukayo", "Saka", 1,
                 birth_date=dt.date(2001, 9, 5), opta_code="p223094")
    players.build_fpl_spine(con)
    row = con.execute("SELECT birth_date, opta_code FROM dim_player").fetchone()
    assert row == (dt.date(2001, 9, 5), "p223094")


def test_build_fpl_spine_is_idempotent(con):
    _fpl_team(con, "2024/25", 1, "Arsenal")
    _fpl_element(con, "2024/25", 11, 223094, "Bukayo", "Saka", 1)

    assert players.build_fpl_spine(con) == 1
    assert players.build_fpl_spine(con) == 0
    assert con.execute("SELECT count(*) FROM dim_player").fetchone()[0] == 1
    assert con.execute("SELECT count(*) FROM map_player_source").fetchone()[0] == 1
