"""football-data.co.uk -- results, match statistics and bookmaker odds.

Free, no key: one CSV per competition-season at /mmz4281/{code}/{comp}.csv,
back to 1993/94. Static files, so this is the one source here with no
scraping fragility -- which is why it goes first.

Column availability varies enormously by era: 1993/94 has 28 columns and no
match statistics at all, 2000/01 has 45 including shots but no kickoff time,
2024/25 has 120. Even within a season the count differs between
competitions. Nothing here may assume a fixed schema; `load` reads by column
name and tolerates absence.

The curated odds set is split into its own table (src_footballdata_odds),
keyed the same as the match row. A match with no 1X2 opening price gets no
odds row.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import duckdb
import requests

from .. import archive, config
from .coerce import as_float, as_int
from .matches import resolve_match

log = logging.getLogger(__name__)

NAME = "footballdata"

BASE = "https://www.football-data.co.uk/mmz4281"

COMPETITIONS: Dict[str, Dict[str, object]] = {
    "E0": {"country": "England", "name": "Premier League", "tier": 1},
    "E1": {"country": "England", "name": "Championship", "tier": 2},
    "SP1": {"country": "Spain", "name": "La Liga", "tier": 1},
    "SP2": {"country": "Spain", "name": "Segunda Division", "tier": 2},
    "I1": {"country": "Italy", "name": "Serie A", "tier": 1},
    "I2": {"country": "Italy", "name": "Serie B", "tier": 2},
    "D1": {"country": "Germany", "name": "Bundesliga", "tier": 1},
    "D2": {"country": "Germany", "name": "2. Bundesliga", "tier": 2},
    "F1": {"country": "France", "name": "Ligue 1", "tier": 1},
    "F2": {"country": "France", "name": "Ligue 2", "tier": 2},
}

BIG_5 = ("E0", "SP1", "I1", "D1", "F1")

_POLITENESS_SECONDS = 1.0


class NotPublishedError(RuntimeError):
    """football-data has no file for this competition-season yet."""


def season_code(start_year: int) -> str:
    """2024 -> '2425', 1999 -> '9900'."""
    return "{:02d}{:02d}".format(start_year % 100, (start_year + 1) % 100)


def season_label(start_year: int) -> str:
    """2024 -> '2024/25'. The warehouse convention, used everywhere."""
    return "{}/{:02d}".format(start_year, (start_year + 1) % 100)


def parse_season(label: str) -> int:
    """'2024/25' -> 2024. Also tolerates the dash form '2024-25' (used by
    vaastav) so a single --season flag works across every source."""
    return int(label.replace("-", "/").split("/")[0])


# --------------------------------------------------------------- ingest
# Network -> archive. Never touches the warehouse.

def decode_csv(raw: bytes) -> str:
    """football-data files carry a UTF-8 BOM, and the older ones are latin-1
    with occasional stray bytes. utf-8-sig handles the BOM; latin-1 never
    fails, so it is a safe last resort."""
    for encoding in ("utf-8-sig", "latin-1"):
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        return text.lstrip("﻿")
    raise ValueError("could not decode payload")


def _fetch_csv(url: str) -> str:
    session = requests.Session()
    session.headers.update({"User-Agent": config.USER_AGENT})
    response = session.get(url, timeout=config.REQUEST_TIMEOUT)
    if response.status_code == 404:
        raise NotPublishedError(url)
    response.raise_for_status()
    if not response.content.strip():
        raise NotPublishedError(url)
    return decode_csv(response.content)


def ingest(
    competitions: Optional[Sequence[str]] = None,
    seasons: Optional[Sequence[int]] = None,
    captured_at: Optional[dt.datetime] = None,
    root: Optional[Path] = None,
    **_ignored,
) -> List[str]:
    competitions = list(competitions or BIG_5)
    seasons = list(seasons or [dt.date.today().year - (0 if dt.date.today().month >= 7 else 1)])
    captured_at = captured_at or dt.datetime.utcnow().replace(microsecond=0)

    unknown = [c for c in competitions if c not in COMPETITIONS]
    if unknown:
        raise ValueError("unknown competition(s): {}".format(", ".join(unknown)))

    written: List[str] = []
    for season in seasons:
        for competition in competitions:
            url = "{}/{}/{}.csv".format(BASE, season_code(season), competition)
            try:
                csv_text = _fetch_csv(url)
            except NotPublishedError:
                log.info("not published yet: %s %s", competition, season_label(season))
                continue

            envelope = {
                "source": NAME,
                "competition": competition,
                "season": season_label(season),
                "url": url,
                "fetched_at": captured_at.isoformat() + "Z",
                "csv": csv_text,
            }
            key = archive.write(
                NAME,
                "season",
                envelope,
                label="{}__{}".format(competition, season_label(season).replace("/", "-")),
                captured_at=captured_at,
                root=root,
            )
            written.append(key)
            log.info("archived %s %s", competition, season_label(season))
            time.sleep(_POLITENESS_SECONDS)

    return written


# ----------------------------------------------------------------- load
# Archive -> src_footballdata_match / src_footballdata_odds. Never touches
# the network.

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
        parsed[target] = as_int(raw) if target in _INT_COLUMNS else raw
    return parsed


def parse_odds(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """One CSV row -> one odds dict, or None if it carries no 1X2 opening price."""
    odds = {target: as_float(_get(row, source)) for source, target in _ODDS_MAP.items()}
    if not any(odds.get(k) is not None for k in _ODDS_PRESENT_KEYS):
        return None
    return odds


def load(con: duckdb.DuckDBPyConnection, payload: dict, archive_key: str) -> int:
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


# ------------------------------------------------------------- identity

def resolve_matches(con: duckdb.DuckDBPyConnection) -> int:
    """Resolve every football-data row into dim_match. Idempotent."""
    rows = con.execute(
        "SELECT competition, season, match_date, kickoff_time, home_team, away_team "
        "FROM src_footballdata_match ORDER BY match_date, home_team"
    ).fetchall()

    resolved = 0
    for competition, season, match_date, kickoff_time, home, away in rows:
        kickoff = dt.datetime.combine(match_date, kickoff_time or dt.time(15, 0))
        source_match_id = "{}|{}|{}|{}|{}".format(
            competition, season, match_date.isoformat(), home, away
        )
        if resolve_match(
            con, source=NAME, source_match_id=source_match_id,
            competition=competition, season=season, kickoff=kickoff,
            home_team=home, away_team=away,
        ) is not None:
            resolved += 1
    return resolved
