import datetime as dt
import json
from pathlib import Path

import pytest

from ptb.core import warehouse
from ptb.core.silver import footballdata as loader

FIXTURE = Path(__file__).parent / "fixtures" / "footballdata_e0_2024_sample.json"


@pytest.fixture
def con(tmp_path):
    connection = warehouse.connect(tmp_path / "test.duckdb")
    yield connection
    connection.close()


@pytest.fixture
def payload():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_parse_date_handles_both_era_formats():
    assert loader.parse_date("16/08/2024") == dt.date(2024, 8, 16)
    assert loader.parse_date("14/08/93") == dt.date(1993, 8, 14)
    assert loader.parse_date("19/08/00") == dt.date(2000, 8, 19)


def test_parse_date_returns_none_for_junk():
    assert loader.parse_date("") is None
    assert loader.parse_date("not a date") is None


def test_load_writes_every_row(con, payload):
    rows = loader.load(con, payload, "some/key.json.gz")
    assert rows == 5
    count = con.execute("SELECT count(*) FROM src_footballdata_match").fetchone()[0]
    assert count == 5


def test_load_maps_columns_correctly(con, payload):
    loader.load(con, payload, "some/key.json.gz")
    row = con.execute(
        "SELECT season, kickoff_time, home_goals, away_goals, result, "
        "       home_shots, away_shots, home_corners, referee "
        "FROM src_footballdata_match WHERE home_team = 'Arsenal'"
    ).fetchone()

    assert row[0] == "2024/25"
    assert row[1] == dt.time(15, 0)
    assert (row[2], row[3], row[4]) == (2, 0, "H")
    assert (row[5], row[6]) == (20, 6)
    assert row[7] == 10
    assert row[8] == "S Hooper"


def test_loading_twice_changes_nothing(con, payload):
    loader.load(con, payload, "some/key.json.gz")
    first = con.execute(
        "SELECT * FROM src_footballdata_match ORDER BY match_date, home_team"
    ).fetchall()

    loader.load(con, payload, "some/key.json.gz")
    second = con.execute(
        "SELECT * FROM src_footballdata_match ORDER BY match_date, home_team"
    ).fetchall()

    assert first == second
    assert con.execute("SELECT count(*) FROM src_footballdata_match").fetchone()[0] == 5


def test_load_tolerates_a_1993_file_with_no_match_stats(con):
    """1993/94 has 28 columns, no Time, no shots, and trailing empty headers."""
    payload = {
        "source": "footballdata",
        "competition": "E0",
        "season": "1993/94",
        "url": "https://www.football-data.co.uk/mmz4281/9394/E0.csv",
        "fetched_at": "2026-08-02T10:15:00Z",
        "csv": (
            "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,,,,\n"
            "E0,14/08/93,Arsenal,Coventry,0,3,A,,,,\n"
            "E0,14/08/93,Liverpool,Sheffield Weds,2,0,H,,,,\n"
        ),
    }
    rows = loader.load(con, payload, "old/key.json.gz")
    assert rows == 2

    row = con.execute(
        "SELECT kickoff_time, home_shots, home_goals, away_goals "
        "FROM src_footballdata_match WHERE home_team = 'Arsenal'"
    ).fetchone()
    assert row == (None, None, 0, 3)


def test_load_skips_blank_trailing_rows(con):
    payload = {
        "source": "footballdata", "competition": "E0", "season": "2024/25",
        "url": "u", "fetched_at": "2026-08-02T10:15:00Z",
        "csv": (
            "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR\n"
            "E0,16/08/2024,Man United,Fulham,1,0,H\n"
            ",,,,,,\n"
            "\n"
        ),
    }
    assert loader.load(con, payload, "k") == 1


def test_loader_is_registered():
    from ptb.core.silver import SOURCES
    assert "footballdata" in SOURCES


def test_rebuild_is_deterministic(con, tmp_path, payload):
    """The property the whole design rests on: replaying the archive twice
    into a fresh warehouse gives byte-identical contents."""
    from ptb.core import archive
    from ptb.core import warehouse

    archive.write("footballdata", "season", payload, label="E0__2024-25",
                  captured_at=dt.datetime(2026, 8, 2, 10, 15, 0))

    warehouse.rebuild(con)
    first = con.execute(
        "SELECT * FROM src_footballdata_match ORDER BY match_date, home_team"
    ).fetchall()

    second_con = warehouse.connect(tmp_path / "second.duckdb")
    warehouse.rebuild(second_con)
    second = second_con.execute(
        "SELECT * FROM src_footballdata_match ORDER BY match_date, home_team"
    ).fetchall()
    second_con.close()

    assert first == second
