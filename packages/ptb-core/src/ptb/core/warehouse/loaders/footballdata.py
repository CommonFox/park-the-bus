"""football-data.co.uk CSV -> src_footballdata_match.

Reads strictly by column name. Column availability varies by era -- 1993/94
has no match statistics and no kickoff time, and its header carries trailing
empty names -- so every optional column is fetched defensively and absence is
normal, not an error.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import logging
from typing import Any, Dict, Optional

import duckdb

from ..load import LOADERS

log = logging.getLogger(__name__)

_DATE_FORMATS = ("%d/%m/%Y", "%d/%m/%y")

# CSV column -> warehouse column
_STATS = {
    "FTHG": "home_goals", "FTAG": "away_goals", "FTR": "result",
    "HTHG": "ht_home_goals", "HTAG": "ht_away_goals", "HTR": "ht_result",
    "HS": "home_shots", "AS": "away_shots",
    "HST": "home_sot", "AST": "away_sot",
    "HF": "home_fouls", "AF": "away_fouls",
    "HC": "home_corners", "AC": "away_corners",
    "HY": "home_yellows", "AY": "away_yellows",
    "HR": "home_reds", "AR": "away_reds",
}
_INT_COLUMNS = {v for k, v in _STATS.items() if k not in ("FTR", "HTR")}
_ODDS = {"B365H": "odds_home", "B365D": "odds_draw", "B365A": "odds_away"}

_COLUMNS = [
    "competition", "season", "match_date", "kickoff_time", "home_team", "away_team",
    "home_goals", "away_goals", "result", "ht_home_goals", "ht_away_goals", "ht_result",
    "home_shots", "away_shots", "home_sot", "away_sot", "home_fouls", "away_fouls",
    "home_corners", "away_corners", "home_yellows", "away_yellows",
    "home_reds", "away_reds", "referee", "odds_home", "odds_draw", "odds_away",
    "archive_key",
]


def parse_date(text: Optional[str]) -> Optional[dt.date]:
    """football-data uses DD/MM/YYYY in modern files and DD/MM/YY in old ones."""
    if not text:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return dt.datetime.strptime(text.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _parse_time(text: Optional[str]) -> Optional[dt.time]:
    if not text:
        return None
    try:
        return dt.datetime.strptime(text.strip(), "%H:%M").time()
    except ValueError:
        return None


def _get(row: Dict[str, Any], column: str) -> Optional[str]:
    value = row.get(column)
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _as_int(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _as_float(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_row(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """One CSV row -> one warehouse row, or None if it is not a real match."""
    home = _get(row, "HomeTeam")
    away = _get(row, "AwayTeam")
    match_date = parse_date(_get(row, "Date"))
    if not home or not away or match_date is None:
        return None

    parsed: Dict[str, Any] = {
        "match_date": match_date,
        "kickoff_time": _parse_time(_get(row, "Time")),
        "home_team": home,
        "away_team": away,
        "referee": _get(row, "Referee"),
    }
    for source_column, target in _STATS.items():
        raw = _get(row, source_column)
        parsed[target] = _as_int(raw) if target in _INT_COLUMNS else raw
    for source_column, target in _ODDS.items():
        parsed[target] = _as_float(_get(row, source_column))
    return parsed


def load_footballdata(
    con: duckdb.DuckDBPyConnection,
    payload: dict,
    archive_key: str,
) -> int:
    competition = payload["competition"]
    season = payload["season"]

    reader = csv.DictReader(io.StringIO(payload["csv"]))
    rows = []
    for raw_row in reader:
        # 1993/94 headers end in empty names, which DictReader maps to None.
        raw_row.pop(None, None)
        raw_row.pop("", None)
        parsed = parse_row(raw_row)
        if parsed is None:
            continue
        parsed["competition"] = competition
        parsed["season"] = season
        parsed["archive_key"] = archive_key
        rows.append([parsed.get(column) for column in _COLUMNS])

    if rows:
        con.executemany(
            "INSERT OR REPLACE INTO src_footballdata_match ({}) VALUES ({})".format(
                ", ".join(_COLUMNS), ", ".join("?" * len(_COLUMNS))
            ),
            rows,
        )
    log.debug("loaded %d rows from %s %s", len(rows), competition, season)
    return len(rows)


LOADERS["footballdata"] = load_footballdata
