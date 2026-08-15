import datetime as dt
import json
from pathlib import Path

import pytest

from ptb.core import warehouse
from ptb.core.silver import footballdata as loader

FIXTURE = Path(__file__).parent / "fixtures" / "footballdata_e0_odds_sample.json"


@pytest.fixture
def con(tmp_path):
    connection = warehouse.connect(tmp_path / "test.duckdb")
    yield connection
    connection.close()


@pytest.fixture
def payload():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_priced_match_gets_an_odds_row(con, payload):
    loader.load(con, payload, "k")
    # two matches loaded, but only the priced one gets an odds row
    assert con.execute("SELECT count(*) FROM src_footballdata_match").fetchone()[0] == 2
    assert con.execute("SELECT count(*) FROM src_footballdata_odds").fetchone()[0] == 1
    assert con.execute(
        "SELECT count(*) FROM src_footballdata_odds WHERE home_team = 'Everton'"
    ).fetchone()[0] == 0


def test_odds_columns_map(con, payload):
    loader.load(con, payload, "k")
    row = con.execute(
        "SELECT competition, season, match_date, b365_h, ps_h, max_h, avg_h, "
        "       b365c_h, psc_h, avgc_h, over25_b365, under25_b365, over25_avg, "
        "       over25_avgc, ah_line, ah_home_avg, ah_home_ps, ahc_line, ahc_home_avg "
        "FROM src_footballdata_odds WHERE home_team = 'Arsenal' AND away_team = 'Wolves'"
    ).fetchone()
    assert row[0] == "E0"
    assert row[1] == "2024/25"
    assert row[2] == dt.date(2024, 8, 17)
    assert row[3] == pytest.approx(1.25)   # b365_h
    assert row[4] == pytest.approx(1.26)   # ps_h
    assert row[5] == pytest.approx(1.28)   # max_h
    assert row[6] == pytest.approx(1.25)   # avg_h (AvgH)
    assert row[7] == pytest.approx(1.22)   # b365c_h (B365CH)
    assert row[8] == pytest.approx(1.23)   # psc_h (PSCH)
    assert row[10] == pytest.approx(1.80)  # over25_b365 (B365>2.5)
    assert row[11] == pytest.approx(2.00)  # under25_b365 (B365<2.5)
    assert row[12] == pytest.approx(1.83)  # over25_avg (Avg>2.5)
    assert row[13] == pytest.approx(1.85)  # over25_avgc (AvgC>2.5)
    assert row[14] == pytest.approx(-1.5)  # ah_line (AHh)
    assert row[15] == pytest.approx(1.93)  # ah_home_avg (AvgAHH)
    assert row[16] == pytest.approx(1.96)  # ah_home_ps (PAHH)
    assert row[17] == pytest.approx(-1.75) # ahc_line (AHCh)
    assert row[18] == pytest.approx(1.99)  # ahc_home_avg (AvgCAHH)


def test_odds_no_longer_on_the_match_table(con, payload):
    loader.load(con, payload, "k")
    columns = {row[1] for row in con.execute("PRAGMA table_info('src_footballdata_match')").fetchall()}
    assert "odds_home" not in columns
    assert "odds_draw" not in columns
    assert "odds_away" not in columns


def test_oddsless_era_writes_no_odds_row(con):
    payload = {
        "source": "footballdata", "competition": "E0", "season": "1993/94",
        "url": "u", "fetched_at": "2026-08-03T10:15:00Z",
        "csv": "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR\n"
               "E0,14/08/93,Arsenal,Coventry,0,3,A\n",
    }
    rows = loader.load(con, payload, "k")
    assert rows == 1
    assert con.execute("SELECT count(*) FROM src_footballdata_match").fetchone()[0] == 1
    assert con.execute("SELECT count(*) FROM src_footballdata_odds").fetchone()[0] == 0


def test_odds_loader_is_idempotent(con, payload):
    loader.load(con, payload, "k")
    loader.load(con, payload, "k")
    assert con.execute("SELECT count(*) FROM src_footballdata_odds").fetchone()[0] == 1
