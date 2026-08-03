import io
from contextlib import redirect_stdout

import pytest

from ptb.core.warehouse import db
from ptb.core.cli import _cmd_coverage
from ptb.core import config


class _Args:
    pass


@pytest.fixture
def warehouse(tmp_path, monkeypatch):
    db_path = tmp_path / "ptb.duckdb"
    monkeypatch.setattr(config, "DB_PATH", db_path)
    con = db.connect(db_path)
    con.execute(
        "INSERT INTO src_footballdata_match "
        "(competition, season, match_date, home_team, away_team, archive_key) "
        "VALUES ('E0', '2024/25', DATE '2024-08-17', 'Arsenal', 'Wolves', 'k')"
    )
    con.execute(
        "INSERT INTO src_understat_match "
        "(understat_match_id, competition, season, kickoff, home_team, away_team, archive_key) "
        "VALUES ('1001', 'E0', '2024/25', TIMESTAMP '2024-08-17 15:00:00', 'Arsenal', 'Wolves', 'k')"
    )
    con.execute(
        "INSERT INTO src_asa_game "
        "(game_id, league, season, kickoff_utc, home_team_id, away_team_id, archive_key) "
        "VALUES ('G1', 'nwsl', '2024', TIMESTAMP '2024-06-15 02:30:00', 'T_POR', 'T_SEA', 'k')"
    )
    con.close()
    return db_path


def test_coverage_reports_all_three_sources(warehouse):
    out = io.StringIO()
    with redirect_stdout(out):
        rc = _cmd_coverage(_Args())
    assert rc == 0
    text = out.getvalue()
    assert "footballdata" in text
    assert "understat" in text
    assert "asa" in text
    assert "NWSL" in text  # asa league mapped to competition
    assert "E0" in text


def test_coverage_reports_empty_warehouse(tmp_path, monkeypatch):
    db_path = tmp_path / "ptb.duckdb"
    monkeypatch.setattr(config, "DB_PATH", db_path)
    db.connect(db_path).close()
    out = io.StringIO()
    with redirect_stdout(out):
        rc = _cmd_coverage(_Args())
    assert rc == 1
    assert "empty" in out.getvalue().lower()
