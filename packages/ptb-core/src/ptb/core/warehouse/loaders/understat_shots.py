"""Understat match payload -> src_understat_shot.

Each shot carries h_team and a_team plus h_a; the shooting team is selected by
h_a so the row records which club actually took the shot.
"""
from __future__ import annotations

from typing import Any, Optional

import duckdb

_SHOT_COLUMNS = [
    "understat_shot_id", "understat_match_id", "minute", "player", "player_id",
    "team", "home_away", "xg", "result", "situation", "shot_type", "x", "y",
    "assist_player", "last_action", "archive_key",
]


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


def load_match_shots(con: duckdb.DuckDBPyConnection, payload: dict, archive_key: str) -> int:
    match_id = str(payload["understat_match_id"])
    shots = (payload.get("data") or {}).get("shots") or {}
    rows = []
    for side in ("h", "a"):
        for shot in shots.get(side, []):
            team = shot.get("h_team") if side == "h" else shot.get("a_team")
            rows.append([
                str(shot["id"]), match_id, _i(shot.get("minute")),
                shot.get("player"), shot.get("player_id"), team, side,
                _f(shot.get("xG")), shot.get("result"), shot.get("situation"),
                shot.get("shotType"), _f(shot.get("X")), _f(shot.get("Y")),
                shot.get("player_assisted"), shot.get("lastAction"), archive_key,
            ])
    if rows:
        con.executemany(
            "INSERT OR REPLACE INTO src_understat_shot ({}) VALUES ({})".format(
                ", ".join(_SHOT_COLUMNS), ", ".join("?" * len(_SHOT_COLUMNS))
            ),
            rows,
        )
    return len(rows)
