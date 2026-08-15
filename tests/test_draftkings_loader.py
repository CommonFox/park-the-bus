import datetime as dt
import json
from pathlib import Path

import pytest

from ptb.core import warehouse
from ptb.core.silver import draftkings as loader

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def con(tmp_path):
    connection = warehouse.connect(tmp_path / "test.duckdb")
    yield connection
    connection.close()


def _load(con):
    payload = json.loads((FIX / "draftkings_markets_sample.json").read_text(encoding="utf-8"))
    return loader.load(con, payload, "draftkings/markets/k.json.gz")


def test_season_from_kickoff():
    assert loader.season_from_kickoff(dt.datetime(2026, 8, 21, 19, 0)) == "2026/27"
    assert loader.season_from_kickoff(dt.datetime(2025, 5, 1, 14, 0)) == "2024/25"


def test_draftkings_is_registered():
    from ptb.core.silver import SOURCES
    assert "draftkings" in SOURCES


def test_loads_only_fully_priced_events(con):
    # Arsenal has 1X2 + O/U 2.5; Everton has 1X2 but no totals -> skipped.
    assert _load(con) == 1
    assert con.execute("SELECT count(*) FROM src_draftkings_odds").fetchone()[0] == 1
    assert con.execute(
        "SELECT count(*) FROM src_draftkings_odds WHERE dk_event_id = '34297695'"
    ).fetchone()[0] == 0


def test_maps_odds_and_metadata(con):
    _load(con)
    row = con.execute(
        "SELECT captured_at, season, kickoff_utc, home_team, away_team, "
        "       moneyline_home, moneyline_draw, moneyline_away, over_2_5, under_2_5 "
        "FROM src_draftkings_odds WHERE dk_event_id = '34297694'"
    ).fetchone()
    assert row[0] == dt.datetime(2026, 8, 3, 12, 0)
    assert row[1] == "2026/27"
    assert row[2] == dt.datetime(2026, 8, 21, 19, 0)
    assert (row[3], row[4]) == ("Arsenal", "Wolves")
    assert row[5] == pytest.approx(1.15)
    assert row[6] == pytest.approx(8.00)
    assert row[7] == pytest.approx(13.00)
    assert row[8] == pytest.approx(1.54)   # over 2.5 only, not the 3.5 line
    assert row[9] == pytest.approx(2.35)


def test_loader_is_idempotent(con):
    _load(con)
    _load(con)
    assert con.execute("SELECT count(*) FROM src_draftkings_odds").fetchone()[0] == 1
