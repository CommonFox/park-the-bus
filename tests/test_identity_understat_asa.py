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
