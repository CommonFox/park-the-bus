import datetime as dt

import pytest

from ptb.core import warehouse
from ptb.core.silver import footballdata, matches


@pytest.fixture
def con(tmp_path):
    connection = warehouse.connect(tmp_path / "test.duckdb")
    yield connection
    connection.close()


def _resolve(con, source, kickoff, source_match_id="m1",
             home="Portland Thorns", away="OL Reign"):
    return matches.resolve_match(
        con, source=source, source_match_id=source_match_id,
        competition="NWSL", season="2025", kickoff=kickoff,
        home_team=home, away_team=away,
    )


def test_resolve_creates_a_match(con):
    match_id = _resolve(con, "fotmob", dt.datetime(2025, 6, 15, 2, 30))
    assert match_id is not None
    assert con.execute("SELECT count(*) FROM dim_match").fetchone()[0] == 1


def test_a_late_nwsl_kickoff_resolves_across_the_utc_date_boundary(con):
    """A 19:30 Pacific Saturday kickoff is 02:30 UTC Sunday. A source
    reporting local date and one reporting UTC must still land on one match."""
    utc = _resolve(con, "fotmob", dt.datetime(2025, 6, 15, 2, 30), "fm-1")
    local = _resolve(con, "asa", dt.datetime(2025, 6, 14, 19, 30), "asa-1")

    assert utc == local
    assert con.execute("SELECT count(*) FROM dim_match").fetchone()[0] == 1
    assert con.execute("SELECT count(*) FROM map_match_source").fetchone()[0] == 2


def test_the_same_source_match_id_is_stable(con):
    first = _resolve(con, "fotmob", dt.datetime(2025, 6, 15, 2, 30), "fm-1")
    second = _resolve(con, "fotmob", dt.datetime(2025, 6, 15, 2, 30), "fm-1")
    assert first == second
    assert con.execute("SELECT count(*) FROM dim_match").fetchone()[0] == 1


def test_fixtures_outside_the_window_are_separate_matches(con):
    first = _resolve(con, "fotmob", dt.datetime(2025, 6, 15, 2, 30), "fm-1")
    second = _resolve(con, "fotmob", dt.datetime(2025, 8, 20, 2, 30), "fm-2")
    assert first != second
    assert con.execute("SELECT count(*) FROM dim_match").fetchone()[0] == 2


def test_reversed_fixture_is_a_different_match(con):
    """Home and away are not interchangeable: the return leg is its own match."""
    home_leg = _resolve(con, "fotmob", dt.datetime(2025, 6, 15, 2, 30), "fm-1")
    away_leg = _resolve(con, "fotmob", dt.datetime(2025, 6, 15, 2, 30), "fm-2",
                        home="OL Reign", away="Portland Thorns")
    assert home_leg != away_leg


def test_ambiguity_is_recorded_rather_than_guessed(con):
    """Two candidates inside the window should not happen, but if it does,
    resolution must fail loudly instead of picking one."""
    _resolve(con, "fotmob", dt.datetime(2025, 6, 15, 2, 30), "fm-1")
    _resolve(con, "fotmob", dt.datetime(2025, 6, 16, 18, 0), "fm-2")

    result = _resolve(con, "asa", dt.datetime(2025, 6, 15, 14, 0), "asa-1")

    assert result is None
    row = con.execute(
        "SELECT source, source_match_id, reason FROM unresolved_match"
    ).fetchone()
    assert row[0] == "asa"
    assert row[1] == "asa-1"
    assert row[2] == matches.MULTIPLE_CANDIDATES


def test_ambiguous_rows_are_not_dropped_from_the_source_table(con):
    """Unresolved means unmapped, never deleted."""
    _resolve(con, "fotmob", dt.datetime(2025, 6, 15, 2, 30), "fm-1")
    _resolve(con, "fotmob", dt.datetime(2025, 6, 16, 18, 0), "fm-2")
    _resolve(con, "asa", dt.datetime(2025, 6, 15, 14, 0), "asa-1")

    assert con.execute("SELECT count(*) FROM unresolved_match").fetchone()[0] == 1
    assert con.execute("SELECT count(*) FROM dim_match").fetchone()[0] == 2


def test_match_key_is_stored_for_debugging(con):
    _resolve(con, "fotmob", dt.datetime(2025, 6, 15, 2, 30))
    key = con.execute("SELECT match_key FROM dim_match").fetchone()[0]
    assert "2025-06-15" in key
    assert "portland thorns" in key


def test_resolve_footballdata_maps_loaded_rows(con):
    con.execute(
        "INSERT INTO src_footballdata_match "
        "(competition, season, match_date, kickoff_time, home_team, away_team, archive_key) "
        "VALUES ('E0', '2024/25', DATE '2024-08-16', TIME '20:00', "
        "        'Man United', 'Fulham', 'k')"
    )
    resolved = footballdata.resolve_matches(con)

    assert resolved == 1
    row = con.execute(
        "SELECT m.competition, m.season, t.canonical_name "
        "FROM dim_match m JOIN dim_team t ON t.team_id = m.home_team_id"
    ).fetchone()
    assert row == ("E0", "2024/25", "Manchester United")


def test_resolve_footballdata_is_idempotent(con):
    con.execute(
        "INSERT INTO src_footballdata_match "
        "(competition, season, match_date, kickoff_time, home_team, away_team, archive_key) "
        "VALUES ('E0', '2024/25', DATE '2024-08-16', TIME '20:00', "
        "        'Man United', 'Fulham', 'k')"
    )
    footballdata.resolve_matches(con)
    footballdata.resolve_matches(con)
    assert con.execute("SELECT count(*) FROM dim_match").fetchone()[0] == 1
