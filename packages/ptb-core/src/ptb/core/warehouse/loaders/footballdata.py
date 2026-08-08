"""football-data.co.uk CSV -> src_footballdata_match and src_footballdata_odds.

Reads strictly by column name. Column availability varies by era -- 1993/94 has
no match statistics and no odds, 2000/01 has shots, 2024/25 has 120 columns
including a large odds block -- so every optional column is fetched defensively
and absence is normal, not an error.

The curated odds set is split into its own table (src_footballdata_odds), keyed
the same as the match row. A match with no 1X2 opening price gets no odds row.
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

# CSV column -> match-table column
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

_MATCH_COLUMNS = [
    "competition", "season", "match_date", "kickoff_time", "home_team", "away_team",
    "home_goals", "away_goals", "result", "ht_home_goals", "ht_away_goals", "ht_result",
    "home_shots", "away_shots", "home_sot", "away_sot", "home_fouls", "away_fouls",
    "home_corners", "away_corners", "home_yellows", "away_yellows",
    "home_reds", "away_reds", "referee", "archive_key",
]

# CSV column -> odds-table column. Absence is normal in older eras.
_ODDS_MAP = {
    "B365H": "b365_h", "B365D": "b365_d", "B365A": "b365_a",
    "PSH": "ps_h", "PSD": "ps_d", "PSA": "ps_a",
    "MaxH": "max_h", "MaxD": "max_d", "MaxA": "max_a",
    "AvgH": "avg_h", "AvgD": "avg_d", "AvgA": "avg_a",
    "B365CH": "b365c_h", "B365CD": "b365c_d", "B365CA": "b365c_a",
    "PSCH": "psc_h", "PSCD": "psc_d", "PSCA": "psc_a",
    "AvgCH": "avgc_h", "AvgCD": "avgc_d", "AvgCA": "avgc_a",
    "B365>2.5": "over25_b365", "B365<2.5": "under25_b365",
    "Avg>2.5": "over25_avg", "Avg<2.5": "under25_avg",
    "AvgC>2.5": "over25_avgc", "AvgC<2.5": "under25_avgc",
    "AHh": "ah_line", "AvgAHH": "ah_home_avg", "AvgAHA": "ah_away_avg",
    "PAHH": "ah_home_ps", "PAHA": "ah_away_ps",
    "AHCh": "ahc_line", "AvgCAHH": "ahc_home_avg", "AvgCAHA": "ahc_away_avg",
    "PCAHH": "ahc_home_ps", "PCAHA": "ahc_away_ps",
}
_ODDS_COLUMNS = (
    ["competition", "season", "match_date", "home_team", "away_team"]
    + list(_ODDS_MAP.values())
    + ["archive_key"]
)
# A 1X2 opening price from any of these means the row is worth an odds row.
_ODDS_PRESENT_KEYS = ("b365_h", "ps_h", "max_h", "avg_h")


def parse_date(text: Optional[str]) -> Optional[dt.date]:
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
    """One CSV row -> one match dict, or None if it is not a real match."""
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
    return parsed


def parse_odds(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """One CSV row -> one odds dict, or None if it carries no 1X2 opening price."""
    odds = {target: _as_float(_get(row, source)) for source, target in _ODDS_MAP.items()}
    if not any(odds.get(k) is not None for k in _ODDS_PRESENT_KEYS):
        return None
    return odds


def load_footballdata(con: duckdb.DuckDBPyConnection, payload: dict, archive_key: str) -> int:
    competition = payload["competition"]
    season = payload["season"]

    reader = csv.DictReader(io.StringIO(payload["csv"]))
    match_rows = []
    odds_rows = []
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
        match_rows.append([parsed.get(column) for column in _MATCH_COLUMNS])

        odds = parse_odds(raw_row)
        if odds is not None:
            odds.update({
                "competition": competition, "season": season,
                "match_date": parsed["match_date"], "home_team": parsed["home_team"],
                "away_team": parsed["away_team"], "archive_key": archive_key,
            })
            odds_rows.append([odds.get(column) for column in _ODDS_COLUMNS])

    if match_rows:
        con.executemany(
            "INSERT OR REPLACE INTO src_footballdata_match ({}) VALUES ({})".format(
                ", ".join(_MATCH_COLUMNS), ", ".join("?" * len(_MATCH_COLUMNS))),
            match_rows)
    if odds_rows:
        con.executemany(
            "INSERT OR REPLACE INTO src_footballdata_odds ({}) VALUES ({})".format(
                ", ".join(_ODDS_COLUMNS), ", ".join("?" * len(_ODDS_COLUMNS))),
            odds_rows)
    log.debug("loaded %d matches, %d odds rows from %s %s",
              len(match_rows), len(odds_rows), competition, season)
    return len(match_rows)


LOADERS["footballdata"] = load_footballdata
