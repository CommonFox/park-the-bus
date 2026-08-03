import datetime as dt

import pytest

from ptb.core.archive import LocalBackend, RawArchive
from ptb.core.warehouse import db, load


@pytest.fixture
def con(tmp_path):
    connection = db.connect(tmp_path / "test.duckdb")
    yield connection
    connection.close()


def _tables(con):
    return {row[0] for row in con.execute("SHOW TABLES").fetchall()}


def test_connect_creates_the_expected_tables(con):
    tables = _tables(con)
    assert {"dim_competition", "src_footballdata_match", "meta_archive_loaded"} <= tables


def test_apply_schema_is_idempotent(con):
    before = _tables(con)
    db.apply_schema(con)
    db.apply_schema(con)
    assert _tables(con) == before


def test_competitions_are_seeded(con):
    count = con.execute("SELECT count(*) FROM dim_competition WHERE competition_id = 'E0'").fetchone()[0]
    assert count == 1


def test_mark_loaded_then_is_loaded(con):
    key = "footballdata/season/E0__2024-25__20260802T101500Z.json.gz"
    assert not load.is_loaded(con, key)
    load.mark_loaded(con, key)
    assert load.is_loaded(con, key)


def test_mark_loaded_twice_keeps_one_row(con):
    key = "footballdata/season/E0__2024-25__20260802T101500Z.json.gz"
    load.mark_loaded(con, key)
    load.mark_loaded(con, key)
    count = con.execute("SELECT count(*) FROM meta_archive_loaded").fetchone()[0]
    assert count == 1


def test_rebuild_over_an_empty_archive_reports_nothing(con, tmp_path):
    archive = RawArchive(LocalBackend(tmp_path / "raw"))
    assert load.rebuild(con, archive) == {}


def test_rebuild_ignores_a_source_with_no_loader(con, tmp_path):
    archive = RawArchive(LocalBackend(tmp_path / "raw"))
    archive.write("mystery", "thing", {"n": 1},
                  captured_at=dt.datetime(2026, 8, 2, 10, 15, 0))
    assert load.rebuild(con, archive) == {}
