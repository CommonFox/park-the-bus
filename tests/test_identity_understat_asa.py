import datetime as dt

import pytest

from ptb.core.identity import matches
from ptb.core.warehouse import db


@pytest.fixture
def con(tmp_path):
    connection = db.connect(tmp_path / "test.duckdb")
    yield connection
    connection.close()


def _insert_understat(con, mid, comp, season, kickoff, home, away):
    con.execute(
        "INSERT INTO src_understat_match "
        "(understat_match_id, competition, season, kickoff, home_team, away_team, archive_key) "
        "VALUES (?, ?, ?, ?, ?, ?, 'k')",
        [mid, comp, season, kickoff, home, away],
    )


def test_resolve_understat_maps_loaded_matches(con):
    _insert_understat(con, "1001", "E0", "2024/25",
                      dt.datetime(2024, 8, 17, 15, 0), "Arsenal", "Wolverhampton Wanderers")
    resolved = matches.resolve_understat(con)
    assert resolved == 1
    assert con.execute("SELECT count(*) FROM dim_match").fetchone()[0] == 1


def test_understat_and_footballdata_resolve_to_one_match(con):
    """The headline cross-source test: the same fixture from two sources must
    land on a single dim_match, so a cross-source join returns a row."""
    con.execute(
        "INSERT INTO src_footballdata_match "
        "(competition, season, match_date, kickoff_time, home_team, away_team, archive_key) "
        "VALUES ('E0', '2024/25', DATE '2024-08-17', TIME '15:00', 'Arsenal', 'Wolves', 'fd')"
    )
    matches.resolve_footballdata(con)

    _insert_understat(con, "1001", "E0", "2024/25",
                      dt.datetime(2024, 8, 17, 15, 0), "Arsenal", "Wolverhampton Wanderers")
    matches.resolve_understat(con)

    assert con.execute("SELECT count(*) FROM dim_match").fetchone()[0] == 1
    sources = con.execute(
        "SELECT count(DISTINCT source) FROM map_match_source"
    ).fetchone()[0]
    assert sources == 2


def test_resolve_understat_is_idempotent(con):
    _insert_understat(con, "1001", "E0", "2024/25",
                      dt.datetime(2024, 8, 17, 15, 0), "Arsenal", "Wolverhampton Wanderers")
    matches.resolve_understat(con)
    matches.resolve_understat(con)
    assert con.execute("SELECT count(*) FROM dim_match").fetchone()[0] == 1


def _insert_asa_team(con, league, team_id, name):
    con.execute(
        "INSERT OR REPLACE INTO src_asa_team (league, team_id, team_name) VALUES (?, ?, ?)",
        [league, team_id, name])


def _insert_asa_game(con, gid, league, season, kickoff, home_id, away_id):
    con.execute(
        "INSERT INTO src_asa_game "
        "(game_id, league, season, kickoff_utc, home_team_id, away_team_id, archive_key) "
        "VALUES (?, ?, ?, ?, ?, ?, 'k')",
        [gid, league, season, kickoff, home_id, away_id])


def test_resolve_asa_maps_games_via_team_names(con):
    _insert_asa_team(con, "nwsl", "T_POR", "Portland Thorns FC")
    _insert_asa_team(con, "nwsl", "T_SEA", "Seattle Reign FC")
    _insert_asa_game(con, "G1", "nwsl", "2024",
                     dt.datetime(2024, 6, 15, 2, 30), "T_POR", "T_SEA")
    resolved = matches.resolve_asa(con)
    assert resolved == 1
    row = con.execute(
        "SELECT m.competition, t.canonical_name FROM dim_match m "
        "JOIN dim_team t ON t.team_id = m.home_team_id"
    ).fetchone()
    assert row == ("NWSL", "Portland Thorns FC")


def test_asa_late_kickoff_resolves_within_window(con):
    """An NWSL game at 02:30 UTC (prior evening Pacific) and a source reporting
    the local Saturday date must resolve to one dim_match."""
    _insert_asa_team(con, "nwsl", "T_POR", "Portland Thorns FC")
    _insert_asa_team(con, "nwsl", "T_SEA", "Seattle Reign FC")
    _insert_asa_game(con, "G_UTC", "nwsl", "2024",
                     dt.datetime(2024, 6, 15, 2, 30), "T_POR", "T_SEA")
    matches.resolve_asa(con)

    other = matches.resolve_match(
        con, source="fotmob", source_match_id="fm-1", competition="NWSL",
        season="2024", kickoff=dt.datetime(2024, 6, 14, 19, 30),
        home_team="Portland Thorns FC", away_team="Seattle Reign FC")

    assert con.execute("SELECT count(*) FROM dim_match").fetchone()[0] == 1
    assert other is not None


def test_resolve_asa_is_idempotent(con):
    _insert_asa_team(con, "nwsl", "T_POR", "Portland Thorns FC")
    _insert_asa_team(con, "nwsl", "T_SEA", "Seattle Reign FC")
    _insert_asa_game(con, "G1", "nwsl", "2024",
                     dt.datetime(2024, 6, 15, 2, 30), "T_POR", "T_SEA")
    matches.resolve_asa(con)
    matches.resolve_asa(con)
    assert con.execute("SELECT count(*) FROM dim_match").fetchone()[0] == 1
