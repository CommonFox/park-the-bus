"""ASA payloads -> src_asa_* tables, routed on payload["resource"].

Reference resources (teams, players) and per-game and per-player-season stat
tables each have their own shape. Player stat tables (goals-added, xgoals,
xpass) are handled in asa_players.py; this module covers reference and game
resources.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any, Optional

import duckdb

from ..load import LOADERS

log = logging.getLogger(__name__)


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


def _date(value: Any) -> Optional[dt.date]:
    if not value:
        return None
    try:
        return dt.datetime.strptime(str(value).strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _utc(value: Any) -> Optional[dt.datetime]:
    """'2024-06-15 02:30:00 UTC' -> naive datetime (already UTC)."""
    if not value:
        return None
    text = str(value).replace(" UTC", "").strip()
    try:
        return dt.datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _load_teams(con, payload, archive_key) -> int:
    league = payload["league"]
    rows = [[league, r["team_id"], r.get("team_name"), r.get("team_short_name"),
             r.get("team_abbreviation")] for r in payload["data"] if r.get("team_id")]
    if rows:
        con.executemany(
            "INSERT OR REPLACE INTO src_asa_team "
            "(league, team_id, team_name, team_short_name, team_abbreviation) "
            "VALUES (?, ?, ?, ?, ?)", rows)
    return len(rows)


def _load_players(con, payload, archive_key) -> int:
    league = payload["league"]
    rows = [[league, r["player_id"], str(r.get("season_name")), r.get("player_name"),
             _date(r.get("birth_date")), r.get("nationality"),
             r.get("primary_general_position")]
            for r in payload["data"] if r.get("player_id") and r.get("season_name")]
    if rows:
        con.executemany(
            "INSERT OR REPLACE INTO src_asa_player "
            "(league, player_id, season, player_name, birth_date, nationality, "
            " primary_general_position) VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    return len(rows)


def _load_games(con, payload, archive_key) -> int:
    league, season = payload["league"], payload["season"]
    rows = [[r["game_id"], league, season, _utc(r.get("date_time_utc")),
             r.get("home_team_id"), r.get("away_team_id"),
             _i(r.get("home_score")), _i(r.get("away_score")),
             _i(r.get("matchday")), r.get("status"), archive_key]
            for r in payload["data"]
            if r.get("game_id") and r.get("home_team_id") and r.get("away_team_id")]
    if rows:
        con.executemany(
            "INSERT OR REPLACE INTO src_asa_game "
            "(game_id, league, season, kickoff_utc, home_team_id, away_team_id, "
            " home_score, away_score, matchday, status, archive_key) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    return len(rows)


def _load_game_xgoals(con, payload, archive_key) -> int:
    league, season = payload["league"], payload["season"]
    rows = [[r["game_id"], league, season, r.get("home_team_id"), r.get("away_team_id"),
             _i(r.get("home_goals")), _i(r.get("away_goals")),
             _f(r.get("home_team_xgoals")), _f(r.get("away_team_xgoals")),
             _f(r.get("home_player_xgoals")), _f(r.get("away_player_xgoals")),
             _f(r.get("home_xpoints")), _f(r.get("away_xpoints")), archive_key]
            for r in payload["data"] if r.get("game_id")]
    if rows:
        con.executemany(
            "INSERT OR REPLACE INTO src_asa_game_xgoals "
            "(game_id, league, season, home_team_id, away_team_id, home_goals, "
            " away_goals, home_team_xgoals, away_team_xgoals, home_player_xgoals, "
            " away_player_xgoals, home_xpoints, away_xpoints, archive_key) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    return len(rows)


_ROUTES = {
    "teams": _load_teams,
    "players": _load_players,
    "games": _load_games,
    "games/xgoals": _load_game_xgoals,
}


def load_asa(con: duckdb.DuckDBPyConnection, payload: dict, archive_key: str) -> int:
    resource = payload.get("resource")
    handler = _ROUTES.get(resource)
    if handler is None:
        from .asa_players import load_player_stats, PLAYER_ROUTES
        if resource in PLAYER_ROUTES:
            return load_player_stats(con, payload, archive_key)
        log.warning("no asa loader for resource %r in %s", resource, archive_key)
        return 0
    return handler(con, payload, archive_key)


LOADERS["asa"] = load_asa
