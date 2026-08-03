"""FPL fixtures and element-summary history -> src_fpl_fixture / src_fpl_player_gw."""
from __future__ import annotations

import datetime as dt
from typing import Any, List, Optional

import duckdb


def _f(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _i(value: Any) -> Optional[int]:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _ts(value: Any) -> Optional[dt.datetime]:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "").strip())
    except ValueError:
        return None


_FIXTURE_COLUMNS = [
    "season", "fixture_id", "code", "event", "kickoff_time", "team_h", "team_a",
    "team_h_score", "team_a_score", "finished", "team_h_difficulty",
    "team_a_difficulty", "archive_key",
]


def load_fixtures(con: duckdb.DuckDBPyConnection, payload: dict, archive_key: str) -> int:
    season = payload["season"]
    rows = []
    for fx in payload["data"]:
        if fx.get("id") is None:
            continue
        rows.append([
            season, _i(fx.get("id")), _i(fx.get("code")), _i(fx.get("event")),
            _ts(fx.get("kickoff_time")), _i(fx.get("team_h")), _i(fx.get("team_a")),
            _i(fx.get("team_h_score")), _i(fx.get("team_a_score")), fx.get("finished"),
            _i(fx.get("team_h_difficulty")), _i(fx.get("team_a_difficulty")), archive_key,
        ])
    if rows:
        con.executemany(
            "INSERT OR REPLACE INTO src_fpl_fixture ({}) VALUES ({})".format(
                ", ".join(_FIXTURE_COLUMNS), ", ".join("?" * len(_FIXTURE_COLUMNS))),
            rows)
    return len(rows)


# --- element-summary history (Task 4 adds the schema and tests) --------------

_GW_COLUMNS = [
    "season", "source", "element_id", "event", "fixture", "opponent_team", "minutes",
    "total_points", "goals_scored", "assists", "clean_sheets", "goals_conceded",
    "own_goals", "penalties_saved", "penalties_missed", "yellow_cards", "red_cards",
    "saves", "bonus", "bps", "influence", "creativity", "threat", "ict_index",
    "expected_goals", "expected_assists", "expected_goal_involvements",
    "expected_goals_conceded", "value", "selected", "transfers_balance",
    "transfers_in", "transfers_out", "was_home", "kickoff_time", "team_h_score",
    "team_a_score", "starts", "player_name", "archive_key",
]


def _gw_row(season: str, source: str, element_id: Optional[int], row: dict,
            player_name: Optional[str], archive_key: str) -> list:
    return [
        season, source, element_id, _i(row.get("round")), _i(row.get("fixture")),
        _i(row.get("opponent_team")), _i(row.get("minutes")), _i(row.get("total_points")),
        _i(row.get("goals_scored")), _i(row.get("assists")), _i(row.get("clean_sheets")),
        _i(row.get("goals_conceded")), _i(row.get("own_goals")), _i(row.get("penalties_saved")),
        _i(row.get("penalties_missed")), _i(row.get("yellow_cards")), _i(row.get("red_cards")),
        _i(row.get("saves")), _i(row.get("bonus")), _i(row.get("bps")), _f(row.get("influence")),
        _f(row.get("creativity")), _f(row.get("threat")), _f(row.get("ict_index")),
        _f(row.get("expected_goals")), _f(row.get("expected_assists")),
        _f(row.get("expected_goal_involvements")), _f(row.get("expected_goals_conceded")),
        _i(row.get("value")), _i(row.get("selected")), _i(row.get("transfers_balance")),
        _i(row.get("transfers_in")), _i(row.get("transfers_out")), row.get("was_home"),
        _ts(row.get("kickoff_time")), _i(row.get("team_h_score")), _i(row.get("team_a_score")),
        _i(row.get("starts")), player_name, archive_key,
    ]


def _insert_gw(con: duckdb.DuckDBPyConnection, rows: List[list]) -> int:
    if rows:
        con.executemany(
            "INSERT OR REPLACE INTO src_fpl_player_gw ({}) VALUES ({})".format(
                ", ".join(_GW_COLUMNS), ", ".join("?" * len(_GW_COLUMNS))),
            rows)
    return len(rows)


def load_element_history(con: duckdb.DuckDBPyConnection, payload: dict, archive_key: str) -> int:
    season = payload["season"]
    element_id = _i(payload.get("element_id"))
    history = (payload.get("data") or {}).get("history", [])
    rows = [_gw_row(season, "api", element_id, h, None, archive_key) for h in history]
    return _insert_gw(con, rows)
