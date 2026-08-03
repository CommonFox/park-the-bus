"""FotMob payloads -> src_fotmob_player_stat / src_fotmob_team_stat.

One payload is one stat's leaderboard for one season and type. The stat's title
and category are read from the payload's statsList by matching the stat name;
the per-row value is under statValue.value and the paired secondary under
substatValue.value.
"""
from __future__ import annotations

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


def _meta(payload: dict) -> tuple:
    """(stat_title, stat_category) for this payload's stat, from statsList."""
    stat = payload["stat"]
    for entry in payload["data"].get("statsList", []):
        if entry.get("name") == stat:
            return entry.get("title"), entry.get("category")
    return None, None


def _value(row: dict, key: str) -> Optional[float]:
    obj = row.get(key) or {}
    return _f(obj.get("value"))


def _load_players(con, payload, archive_key) -> int:
    league_name = (payload["data"].get("leagueDetails") or {}).get("name")
    title, category = _meta(payload)
    rows = []
    for r in payload["data"].get("statsData", []):
        if r.get("id") is None:
            continue
        rows.append([
            payload["league_id"], league_name, payload["season"], payload.get("season_id"),
            payload["stat"], title, category, _i(r.get("id")), _i(r.get("teamId")),
            r.get("name"), _i(r.get("position")), _value(r, "statValue"),
            _value(r, "substatValue"), _i(r.get("rank")), archive_key,
        ])
    if rows:
        con.executemany(
            "INSERT OR REPLACE INTO src_fotmob_player_stat "
            "(league_id, league_name, season, season_id, stat_name, stat_title, stat_category, "
            " fotmob_player_id, fotmob_team_id, player_name, position_code, value, substat_value, "
            " rank, archive_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    return len(rows)


def _load_teams(con, payload, archive_key) -> int:
    league_name = (payload["data"].get("leagueDetails") or {}).get("name")
    title, category = _meta(payload)
    rows = []
    for r in payload["data"].get("statsData", []):
        if r.get("id") is None:
            continue
        rows.append([
            payload["league_id"], league_name, payload["season"], payload.get("season_id"),
            payload["stat"], title, category, _i(r.get("id")), r.get("name"),
            _value(r, "statValue"), _value(r, "substatValue"), _i(r.get("rank")), archive_key,
        ])
    if rows:
        con.executemany(
            "INSERT OR REPLACE INTO src_fotmob_team_stat "
            "(league_id, league_name, season, season_id, stat_name, stat_title, stat_category, "
            " fotmob_team_id, team_name, value, substat_value, rank, archive_key) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    return len(rows)


def load_fotmob(con: duckdb.DuckDBPyConnection, payload: dict, archive_key: str) -> int:
    stat_type = payload.get("stat_type")
    if stat_type == "players":
        return _load_players(con, payload, archive_key)
    if stat_type == "teams":
        return _load_teams(con, payload, archive_key)
    log.warning("unknown fotmob stat_type %r in %s", stat_type, archive_key)
    return 0


LOADERS["fotmob"] = load_fotmob
