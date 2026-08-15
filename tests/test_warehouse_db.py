import datetime as dt

import pytest

from ptb.core import archive
from ptb.core import warehouse


@pytest.fixture
def con(tmp_path):
    connection = warehouse.connect(tmp_path / "test.duckdb")
    yield connection
    connection.close()


def _tables(con):
    return {row[0] for row in con.execute("SHOW TABLES").fetchall()}


def test_connect_creates_the_expected_tables(con):
    tables = _tables(con)
    assert {"dim_competition", "src_footballdata_match", "meta_archive_loaded"} <= tables


def test_apply_schema_is_idempotent(con):
    before = _tables(con)
    warehouse.apply_schema(con)
    warehouse.apply_schema(con)
    assert _tables(con) == before


def test_competitions_are_seeded(con):
    count = con.execute("SELECT count(*) FROM dim_competition WHERE competition_id = 'E0'").fetchone()[0]
    assert count == 1


def test_mark_loaded_then_is_loaded(con):
    key = "footballdata/season/E0__2024-25__20260802T101500Z.json.gz"
    assert not warehouse.is_loaded(con, key)
    warehouse.mark_loaded(con, key)
    assert warehouse.is_loaded(con, key)


def test_mark_loaded_twice_keeps_one_row(con):
    key = "footballdata/season/E0__2024-25__20260802T101500Z.json.gz"
    warehouse.mark_loaded(con, key)
    warehouse.mark_loaded(con, key)
    count = con.execute("SELECT count(*) FROM meta_archive_loaded").fetchone()[0]
    assert count == 1


def test_rebuild_over_an_empty_archive_reports_nothing(con, tmp_path):
    assert warehouse.rebuild(con) == {}


def test_rebuild_ignores_a_source_with_no_loader(con, tmp_path):
    archive.write("mystery", "thing", {"n": 1},
                  captured_at=dt.datetime(2026, 8, 2, 10, 15, 0))
    assert warehouse.rebuild(con) == {}


def test_player_identity_tables_exist(tmp_path):
    from ptb.core import warehouse

    con = warehouse.connect(tmp_path / "test.duckdb")
    try:
        names = {r[0] for r in con.execute(
            "SELECT table_name FROM information_schema.tables").fetchall()}
        assert {"dim_player", "map_player_source", "unresolved_player"} <= names
        assert con.execute("SELECT nextval('seq_player_id')").fetchone()[0] == 1
    finally:
        con.close()


def test_dim_player_allows_homonyms(tmp_path):
    """Two different Danny Wards have played in the Premier League, so
    normalized_name carries no UNIQUE constraint -- unlike dim_team."""
    from ptb.core import warehouse

    con = warehouse.connect(tmp_path / "test.duckdb")
    try:
        con.execute(
            "INSERT INTO dim_player (player_id, canonical_name, normalized_name) "
            "VALUES (1, 'Danny Ward', 'danny ward'), (2, 'Danny Ward', 'danny ward')")
        assert con.execute("SELECT count(*) FROM dim_player").fetchone()[0] == 2
    finally:
        con.close()
