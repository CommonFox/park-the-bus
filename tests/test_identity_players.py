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


def _candidate(player_id=1, name="Bukayo Saka", birth_date=None,
               opta_code=None, team_name="Arsenal"):
    return players.Candidate(player_id, name, birth_date, opta_code, team_name)


def test_opta_code_wins_outright():
    result = players.match_player(
        [_candidate(opta_code="p223094"), _candidate(player_id=2, name="Someone Else")],
        name="Totally Different", team_name=None,
        birth_date=None, opta_code="p223094")
    assert (result.player_id, result.method, result.confidence) == (1, "opta_code", 1.00)


def test_name_plus_birth_date_matches():
    result = players.match_player(
        [_candidate(birth_date=dt.date(2001, 9, 5))],
        name="Bukayo Saka", team_name=None,
        birth_date=dt.date(2001, 9, 5), opta_code=None)
    assert (result.method, result.confidence) == ("name_dob", 0.99)


def test_name_plus_team_matches():
    result = players.match_player(
        [_candidate()], name="Bukayo Saka", team_name="Arsenal",
        birth_date=None, opta_code=None)
    assert (result.method, result.confidence) == ("name_team_season", 0.95)


def test_two_players_of_the_same_name_at_the_same_club_are_ambiguous():
    """The homonym case. Two Danny Wards at one club cannot be separated by
    name and team, so the tier must not fire and the fallback must refuse."""
    result = players.match_player(
        [_candidate(player_id=1, name="Danny Ward"),
         _candidate(player_id=2, name="Danny Ward")],
        name="Danny Ward", team_name="Arsenal",
        birth_date=None, opta_code=None)
    assert result.player_id is None
    assert result.reason == players.AMBIGUOUS


def test_two_players_of_the_same_name_split_by_birth_date():
    """Same names, same club, but a birth date separates them cleanly."""
    result = players.match_player(
        [_candidate(player_id=1, name="Danny Ward", birth_date=dt.date(1993, 6, 22)),
         _candidate(player_id=2, name="Danny Ward", birth_date=dt.date(1990, 12, 1))],
        name="Danny Ward", team_name="Arsenal",
        birth_date=dt.date(1990, 12, 1), opta_code=None)
    assert result.player_id == 2
    assert result.method == "name_dob"


def _understat_match(con, match_id, season, competition="E0"):
    con.execute(
        "INSERT OR REPLACE INTO src_understat_match (understat_match_id, competition, "
        "season, home_team, away_team, archive_key) VALUES (?, ?, ?, 'Arsenal', 'Wolves', 'k')",
        [match_id, competition, season])


def _understat_shot(con, shot_id, match_id, player_id, player, team):
    con.execute(
        "INSERT OR REPLACE INTO src_understat_shot (understat_shot_id, understat_match_id, "
        "minute, player, player_id, team, home_away, archive_key) "
        "VALUES (?, ?, 10, ?, ?, ?, 'h', 'k')",
        [shot_id, match_id, player, player_id, team])


def test_understat_player_matches_the_fpl_spine(con):
    _fpl_team(con, "2024/25", 1, "Arsenal")
    _fpl_element(con, "2024/25", 11, 223094, "Bukayo", "Saka", 1)
    players.build_fpl_spine(con)

    _understat_match(con, "1001", "2024/25")
    _understat_shot(con, "s1", "1001", "647", "Bukayo Saka", "Arsenal")

    assert players.resolve_understat_players(con) == 1
    row = con.execute(
        "SELECT player_id, method FROM map_player_source "
        "WHERE source = 'understat' AND source_player_id = '647'").fetchone()
    fpl_player_id = con.execute(
        "SELECT player_id FROM dim_player WHERE fpl_code = 223094").fetchone()[0]
    assert row[0] == fpl_player_id
    assert con.execute("SELECT count(*) FROM dim_player").fetchone()[0] == 1


def test_a_shortened_fpl_name_still_matches(con):
    """FPL stores 'Gabriel Jesus'; Understat stores the full legal name."""
    _fpl_team(con, "2024/25", 1, "Arsenal")
    _fpl_element(con, "2024/25", 12, 205651, "Gabriel", "Jesus", 1)
    players.build_fpl_spine(con)

    _understat_match(con, "1001", "2024/25")
    _understat_shot(con, "s1", "1001", "700", "Gabriel Fernando de Jesus", "Arsenal")

    assert players.resolve_understat_players(con) == 1


def test_a_player_absent_from_fpl_gets_a_new_row(con):
    """A La Liga player has no FPL code. Creating the row is how the dimension
    grows beyond the Premier League -- it is not a resolution failure."""
    _understat_match(con, "2001", "2024/25", competition="SP1")
    _understat_shot(con, "s1", "2001", "900", "Robert Lewandowski", "Barcelona")

    assert players.resolve_understat_players(con) == 0
    row = con.execute(
        "SELECT p.canonical_name, m.method FROM dim_player p "
        "JOIN map_player_source m ON m.player_id = p.player_id "
        "WHERE m.source = 'understat'").fetchone()
    assert row == ("Robert Lewandowski", "created")


def test_an_ambiguous_understat_player_is_recorded_not_guessed(con):
    _fpl_team(con, "2024/25", 1, "Arsenal")
    _fpl_element(con, "2024/25", 20, 111111, "Danny", "Ward", 1)
    _fpl_element(con, "2024/25", 21, 222222, "Danny", "Ward", 1)
    players.build_fpl_spine(con)

    _understat_match(con, "1001", "2024/25")
    _understat_shot(con, "s1", "1001", "800", "Danny Ward", "Arsenal")

    assert players.resolve_understat_players(con) == 0
    assert con.execute(
        "SELECT reason FROM unresolved_player WHERE source = 'understat'"
    ).fetchone()[0] == players.AMBIGUOUS
    assert con.execute(
        "SELECT count(*) FROM map_player_source WHERE source = 'understat'"
    ).fetchone()[0] == 0


def test_a_player_in_two_seasons_maps_once(con):
    _fpl_team(con, "2023/24", 1, "Arsenal")
    _fpl_team(con, "2024/25", 1, "Arsenal")
    _fpl_element(con, "2023/24", 7, 223094, "Bukayo", "Saka", 1)
    _fpl_element(con, "2024/25", 11, 223094, "Bukayo", "Saka", 1)
    players.build_fpl_spine(con)

    _understat_match(con, "1001", "2023/24")
    _understat_match(con, "1002", "2024/25")
    _understat_shot(con, "s1", "1001", "647", "Bukayo Saka", "Arsenal")
    _understat_shot(con, "s2", "1002", "647", "Bukayo Saka", "Arsenal")

    players.resolve_understat_players(con)
    assert con.execute(
        "SELECT count(*) FROM map_player_source WHERE source = 'understat'"
    ).fetchone()[0] == 1
