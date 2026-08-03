import datetime as dt

import pytest

from ptb.core.identity import matches
from ptb.core.warehouse import db


@pytest.fixture
def con(tmp_path):
    connection = db.connect(tmp_path / "test.duckdb")
    yield connection
    connection.close()


def _fpl_team(con, season, tid, name):
    con.execute(
        "INSERT OR REPLACE INTO src_fpl_team (season, team_id, name, archive_key) "
        "VALUES (?, ?, ?, 'k')", [season, tid, name])


def _fpl_fixture(con, season, fid, kickoff, team_h, team_a):
    con.execute(
        "INSERT INTO src_fpl_fixture (season, fixture_id, event, kickoff_time, team_h, "
        "team_a, archive_key) VALUES (?, ?, 1, ?, ?, ?, 'k')",
        [season, fid, kickoff, team_h, team_a])


def test_resolve_fpl_maps_fixtures_via_team_names(con):
    _fpl_team(con, "2024/25", 1, "Arsenal")
    _fpl_team(con, "2024/25", 20, "Wolves")
    _fpl_fixture(con, "2024/25", 1, dt.datetime(2024, 8, 17, 14, 0), 1, 20)
    resolved = matches.resolve_fpl(con)
    assert resolved == 1
    assert con.execute("SELECT count(*) FROM dim_match").fetchone()[0] == 1


def test_three_sources_resolve_to_one_match(con):
    """The headline test: football-data, Understat and FPL all land on one
    dim_match for the same Premier League fixture."""
    con.execute(
        "INSERT INTO src_footballdata_match "
        "(competition, season, match_date, kickoff_time, home_team, away_team, archive_key) "
        "VALUES ('E0', '2024/25', DATE '2024-08-17', TIME '14:00', 'Arsenal', 'Wolves', 'fd')"
    )
    matches.resolve_footballdata(con)

    con.execute(
        "INSERT INTO src_understat_match "
        "(understat_match_id, competition, season, kickoff, home_team, away_team, archive_key) "
        "VALUES ('1001', 'E0', '2024/25', TIMESTAMP '2024-08-17 15:00:00', "
        "        'Arsenal', 'Wolverhampton Wanderers', 'us')"
    )
    matches.resolve_understat(con)

    _fpl_team(con, "2024/25", 1, "Arsenal")
    _fpl_team(con, "2024/25", 20, "Wolves")
    _fpl_fixture(con, "2024/25", 1, dt.datetime(2024, 8, 17, 14, 0), 1, 20)
    matches.resolve_fpl(con)

    assert con.execute("SELECT count(*) FROM dim_match").fetchone()[0] == 1
    sources = con.execute("SELECT count(DISTINCT source) FROM map_match_source").fetchone()[0]
    assert sources == 3


def test_resolve_fpl_is_idempotent(con):
    _fpl_team(con, "2024/25", 1, "Arsenal")
    _fpl_team(con, "2024/25", 20, "Wolves")
    _fpl_fixture(con, "2024/25", 1, dt.datetime(2024, 8, 17, 14, 0), 1, 20)
    matches.resolve_fpl(con)
    matches.resolve_fpl(con)
    assert con.execute("SELECT count(*) FROM dim_match").fetchone()[0] == 1
