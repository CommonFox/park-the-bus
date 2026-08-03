import datetime as dt

import pytest

from ptb.core.identity import matches
from ptb.core.warehouse import db


@pytest.fixture
def con(tmp_path):
    connection = db.connect(tmp_path / "test.duckdb")
    yield connection
    connection.close()


def _dk(con, event_id, captured_at, home, away, kickoff):
    con.execute(
        "INSERT INTO src_draftkings_odds "
        "(dk_event_id, captured_at, season, kickoff_utc, home_team, away_team, archive_key) "
        "VALUES (?, ?, '2026/27', ?, ?, ?, 'k')",
        [event_id, captured_at, kickoff, home, away])


def test_resolve_draftkings_maps_events(con):
    _dk(con, "E1", dt.datetime(2026, 8, 3, 12, 0), "Arsenal", "Wolves",
        dt.datetime(2026, 8, 21, 19, 0))
    assert matches.resolve_draftkings(con) == 1
    assert con.execute("SELECT count(*) FROM dim_match").fetchone()[0] == 1


def test_resolve_uses_latest_snapshot_only(con):
    # two snapshots of one event must still be one dim_match / one map row
    _dk(con, "E1", dt.datetime(2026, 8, 3, 12, 0), "Arsenal", "Wolves",
        dt.datetime(2026, 8, 21, 19, 0))
    _dk(con, "E1", dt.datetime(2026, 8, 3, 18, 0), "Arsenal", "Wolves",
        dt.datetime(2026, 8, 21, 19, 0))
    matches.resolve_draftkings(con)
    assert con.execute("SELECT count(*) FROM dim_match").fetchone()[0] == 1
    assert con.execute(
        "SELECT count(*) FROM map_match_source WHERE source = 'draftkings'"
    ).fetchone()[0] == 1


def test_draftkings_and_fpl_resolve_to_one_match(con):
    """A DraftKings event and the FPL fixture for the same upcoming match land
    on one dim_match."""
    con.execute(
        "INSERT INTO src_fpl_team (season, team_id, name, archive_key) VALUES "
        "('2026/27', 1, 'Arsenal', 'k'), ('2026/27', 20, 'Wolves', 'k')")
    con.execute(
        "INSERT INTO src_fpl_fixture (season, fixture_id, event, kickoff_time, team_h, "
        "team_a, archive_key) VALUES ('2026/27', 1, 1, TIMESTAMP '2026-08-21 19:00:00', 1, 20, 'k')")
    matches.resolve_fpl(con)

    _dk(con, "E1", dt.datetime(2026, 8, 3, 12, 0), "Arsenal", "Wolves",
        dt.datetime(2026, 8, 21, 19, 0))
    matches.resolve_draftkings(con)

    assert con.execute("SELECT count(*) FROM dim_match").fetchone()[0] == 1
    assert con.execute("SELECT count(DISTINCT source) FROM map_match_source").fetchone()[0] == 2


def test_resolve_draftkings_is_idempotent(con):
    _dk(con, "E1", dt.datetime(2026, 8, 3, 12, 0), "Arsenal", "Wolves",
        dt.datetime(2026, 8, 21, 19, 0))
    matches.resolve_draftkings(con)
    matches.resolve_draftkings(con)
    assert con.execute("SELECT count(*) FROM dim_match").fetchone()[0] == 1
