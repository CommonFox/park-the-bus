import json
from pathlib import Path

import pytest

from ptb.core.warehouse import db
from ptb.core.warehouse.loaders import vaastav as loader

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def con(tmp_path):
    connection = db.connect(tmp_path / "test.duckdb")
    yield connection
    connection.close()


def _load(con):
    payload = json.loads((FIX / "vaastav_2024_25_sample.json").read_text(encoding="utf-8"))
    return loader.load_vaastav(con, payload, "vaastav/season/2024-25__k.json.gz")


def test_vaastav_is_registered():
    from ptb.core.warehouse.load import LOADERS
    assert "vaastav" in LOADERS


def test_loads_player_season_and_gw(con):
    _load(con)
    assert con.execute("SELECT count(*) FROM src_fpl_element_season").fetchone()[0] == 1
    assert con.execute(
        "SELECT count(*) FROM src_fpl_player_gw WHERE source = 'vaastav'"
    ).fetchone()[0] == 1


def test_player_season_columns(con):
    _load(con)
    row = con.execute(
        "SELECT code, web_name, element_type, team, total_points, minutes "
        "FROM src_fpl_element_season WHERE season = '2024/25' AND element_id = 11"
    ).fetchone()
    assert row == (223094, "Saka", 3, 1, 200, 3000)


def test_gw_columns_and_player_name(con):
    _load(con)
    row = con.execute(
        "SELECT event, minutes, total_points, goals_scored, assists, value, "
        "       was_home, player_name FROM src_fpl_player_gw "
        "WHERE source = 'vaastav' AND element_id = 11 AND event = 1"
    ).fetchone()
    assert row[0] == 1
    assert (row[1], row[2]) == (90, 13)
    assert (row[3], row[4]) == (1, 1)
    assert row[5] == 100
    assert row[6] is True
    assert row[7] == "Bukayo Saka"


def test_vaastav_loader_is_idempotent(con):
    _load(con)
    _load(con)
    assert con.execute("SELECT count(*) FROM src_fpl_element_season").fetchone()[0] == 1
    assert con.execute("SELECT count(*) FROM src_fpl_player_gw").fetchone()[0] == 1
