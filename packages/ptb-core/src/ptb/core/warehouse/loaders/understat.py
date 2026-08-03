"""Understat payloads -> src_understat_match / src_understat_shot.

Two payload shapes share one loader, routed on payload["endpoint"]:
- "league" -> one row per dated match (match-level xG)
- "match"  -> one row per shot

Understat sends every numeric value as a string; each is coerced defensively
so a stray empty string never aborts a rebuild.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any, Optional

import duckdb

from ..load import LOADERS

log = logging.getLogger(__name__)

_MATCH_COLUMNS = [
    "understat_match_id", "competition", "season", "kickoff", "home_team", "away_team",
    "home_goals", "away_goals", "home_xg", "away_xg", "forecast_w", "forecast_d",
    "forecast_l", "archive_key",
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


def _dttm(value: Any) -> Optional[dt.datetime]:
    if not value:
        return None
    try:
        return dt.datetime.strptime(str(value).strip(), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _load_league(con: duckdb.DuckDBPyConnection, payload: dict, archive_key: str) -> int:
    competition = payload["competition"]
    season = payload["season"]
    rows = []
    for entry in payload["data"].get("dates", []):
        if not entry.get("isResult"):
            continue
        home = (entry.get("h") or {}).get("title")
        away = (entry.get("a") or {}).get("title")
        if not home or not away:
            continue
        goals = entry.get("goals") or {}
        xg = entry.get("xG") or {}
        forecast = entry.get("forecast") or {}
        rows.append([
            str(entry["id"]), competition, season, _dttm(entry.get("datetime")),
            home, away, _i(goals.get("h")), _i(goals.get("a")),
            _f(xg.get("h")), _f(xg.get("a")),
            _f(forecast.get("w")), _f(forecast.get("d")), _f(forecast.get("l")),
            archive_key,
        ])
    if rows:
        con.executemany(
            "INSERT OR REPLACE INTO src_understat_match ({}) VALUES ({})".format(
                ", ".join(_MATCH_COLUMNS), ", ".join("?" * len(_MATCH_COLUMNS))
            ),
            rows,
        )
    return len(rows)


def load_understat(con: duckdb.DuckDBPyConnection, payload: dict, archive_key: str) -> int:
    endpoint = payload.get("endpoint")
    if endpoint == "league":
        return _load_league(con, payload, archive_key)
    if endpoint == "match":
        from .understat_shots import load_match_shots
        return load_match_shots(con, payload, archive_key)
    log.warning("unknown understat endpoint %r in %s", endpoint, archive_key)
    return 0


LOADERS["understat"] = load_understat
