"""ASA per-player-season stat payloads -> src_asa_player_* tables.

goals-added nests a per-action-type array, exploded to one row per action.
xgoals and xpass are flat, one row per player-season.
"""
from __future__ import annotations

from typing import Any, Optional

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


def _load_goals_added(con, payload, archive_key) -> int:
    league, season = payload["league"], payload["season"]
    rows = []
    for r in payload["data"]:
        pid = r.get("player_id")
        if not pid:
            continue
        for action in r.get("data", []):
            rows.append([
                league, season, pid, r.get("team_id"), r.get("general_position"),
                _i(r.get("minutes_played")), action.get("action_type"),
                _f(action.get("goals_added_raw")), _f(action.get("goals_added_above_avg")),
                _i(action.get("count_actions")), archive_key,
            ])
    if rows:
        con.executemany(
            "INSERT OR REPLACE INTO src_asa_player_goals_added "
            "(league, season, player_id, team_id, general_position, minutes_played, "
            " action_type, goals_added_raw, goals_added_above_avg, count_actions, archive_key) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    return len(rows)


def _load_xgoals(con, payload, archive_key) -> int:
    league, season = payload["league"], payload["season"]
    rows = [[league, season, r["player_id"], r.get("team_id"), r.get("general_position"),
             _i(r.get("minutes_played")), _i(r.get("shots")), _i(r.get("shots_on_target")),
             _i(r.get("goals")), _f(r.get("xgoals")), _f(r.get("xplace")),
             _i(r.get("key_passes")), _i(r.get("primary_assists")), _f(r.get("xassists")),
             _i(r.get("goals_plus_primary_assists")), _f(r.get("xgoals_plus_xassists")),
             _f(r.get("points_added")), _f(r.get("xpoints_added")), archive_key]
            for r in payload["data"] if r.get("player_id")]
    if rows:
        con.executemany(
            "INSERT OR REPLACE INTO src_asa_player_xgoals "
            "(league, season, player_id, team_id, general_position, minutes_played, shots, "
            " shots_on_target, goals, xgoals, xplace, key_passes, primary_assists, xassists, "
            " goals_plus_primary_assists, xgoals_plus_xassists, points_added, xpoints_added, "
            " archive_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    return len(rows)


def _load_xpass(con, payload, archive_key) -> int:
    league, season = payload["league"], payload["season"]
    rows = [[league, season, r["player_id"], r.get("team_id"), r.get("general_position"),
             _i(r.get("minutes_played")), _i(r.get("attempted_passes")),
             _f(r.get("pass_completion_percentage")), _f(r.get("xpass_completion_percentage")),
             _f(r.get("passes_completed_over_expected")),
             _f(r.get("passes_completed_over_expected_p100")),
             _f(r.get("avg_distance_yds")), _f(r.get("avg_vertical_distance_yds")),
             _f(r.get("share_team_touches")), _i(r.get("count_games")), archive_key]
            for r in payload["data"] if r.get("player_id")]
    if rows:
        con.executemany(
            "INSERT OR REPLACE INTO src_asa_player_xpass "
            "(league, season, player_id, team_id, general_position, minutes_played, "
            " attempted_passes, pass_completion_percentage, xpass_completion_percentage, "
            " passes_completed_over_expected, passes_completed_over_expected_p100, "
            " avg_distance_yds, avg_vertical_distance_yds, share_team_touches, count_games, "
            " archive_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    return len(rows)


PLAYER_ROUTES = {
    "players/goals-added": _load_goals_added,
    "players/xgoals": _load_xgoals,
    "players/xpass": _load_xpass,
}


def load_player_stats(con: duckdb.DuckDBPyConnection, payload: dict, archive_key: str) -> int:
    return PLAYER_ROUTES[payload["resource"]](con, payload, archive_key)
