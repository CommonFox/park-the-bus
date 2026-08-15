"""vaastav/Fantasy-Premier-League -- historical FPL backfill.

The official API serves only the current season. This community repo publishes
cleaned per-gameweek CSVs back to 2016/17. One payload per season bundles
players_raw.csv and gws/merged_gw.csv, wrapped verbatim. Static history, so a
season already archived is skipped unless `refetch`.

This source owns no tables of its own (hence no vaastav.sql): it backfills
src_fpl_element_season and src_fpl_player_gw, reusing fpl.py's row builder so
both sources land in the same shape. Columns vary by era, so every field is
read by name and tolerated when absent.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import logging
import random
import time
from pathlib import Path
from typing import List, Optional, Sequence, Set

import duckdb
import requests

from .. import archive, config
from .coerce import as_int
from .fpl import gw_row, insert_gw

log = logging.getLogger(__name__)

NAME = "vaastav"

RAW_BASE = "https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data"
KNOWN_SEASONS = (
    "2016-17", "2017-18", "2018-19", "2019-20", "2020-21",
    "2021-22", "2022-23", "2023-24", "2024-25", "2025-26",
)
_POLITENESS_SECONDS = 0.5


def season_label(dash: str) -> str:
    """'2024-25' -> '2024/25'."""
    start, end = dash.split("-")
    return "{}/{}".format(start, end)


def to_dash(season) -> str:
    """Normalise a season to vaastav's dash form '2024-25'.

    Accepts an int start year (2024, as the CLI produces via parse_season), the
    slash form '2024/25', or the dash form already."""
    if isinstance(season, int):
        return "{}-{}".format(season, str(season + 1)[2:])
    text = str(season)
    if "/" in text:
        start = int(text.split("/")[0])
        return "{}-{}".format(start, str(start + 1)[2:])
    return text


# --------------------------------------------------------------- ingest
# Network -> archive. Never touches the warehouse.

def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": config.USER_AGENT})
    return session


def _fetch_csv(session: requests.Session, url: str, retries: int = 3) -> str:
    last: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            resp = session.get(url, timeout=config.REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as exc:
            last = exc
            if attempt < retries:
                time.sleep((2 ** attempt) + random.random())
    raise RuntimeError("failed to fetch {}: {}".format(url, last))


def archived_seasons(root: Optional[Path] = None) -> Set[str]:
    prefix = "vaastav/season/"
    seasons: Set[str] = set()
    for key in archive.keys(source=NAME, endpoint="season", root=root):
        seasons.add(key[len(prefix):].split("__", 1)[0])
    return seasons


def ingest(
    seasons: Optional[Sequence[str]] = None,
    refetch: bool = False,
    captured_at: Optional[dt.datetime] = None,
    root: Optional[Path] = None,
    **_ignored,
) -> List[str]:
    seasons = [to_dash(s) for s in (seasons or KNOWN_SEASONS)]
    captured_at = captured_at or dt.datetime.utcnow().replace(microsecond=0)
    session = _session()
    done = set() if refetch else archived_seasons(root)
    written: List[str] = []

    for dash in seasons:
        if dash in done:
            continue
        players = _fetch_csv(session, "{}/{}/players_raw.csv".format(RAW_BASE, dash))
        merged = _fetch_csv(session, "{}/{}/gws/merged_gw.csv".format(RAW_BASE, dash))
        envelope = {
            "source": NAME, "endpoint": "season", "season": season_label(dash),
            "url": "{}/{}/".format(RAW_BASE, dash),
            "fetched_at": captured_at.isoformat() + "Z",
            "data": {"players_raw": players, "merged_gw": merged},
        }
        written.append(archive.write(
            NAME, "season", envelope, label=dash, captured_at=captured_at, root=root))
        log.info("archived vaastav %s", dash)
        time.sleep(_POLITENESS_SECONDS)

    return written


# ----------------------------------------------------------------- load
# Archive -> src_fpl_element_season / src_fpl_player_gw. Never touches the
# network.

_SEASON_COLUMNS = [
    "season", "element_id", "code", "first_name", "second_name", "web_name",
    "element_type", "team", "team_code", "now_cost", "total_points", "minutes",
    "archive_key",
]


def _get(row: dict, key: str) -> Optional[str]:
    value = row.get(key)
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _load_players_raw(con, season, csv_text, archive_key) -> int:
    rows = []
    for r in csv.DictReader(io.StringIO(csv_text)):
        eid = as_int(r.get("id"))
        if eid is None:
            continue
        rows.append([
            season, eid, as_int(r.get("code")), _get(r, "first_name"), _get(r, "second_name"),
            _get(r, "web_name"), as_int(r.get("element_type")), as_int(r.get("team")),
            as_int(r.get("team_code")), as_int(r.get("now_cost")), as_int(r.get("total_points")),
            as_int(r.get("minutes")), archive_key,
        ])
    if rows:
        con.executemany(
            "INSERT OR REPLACE INTO src_fpl_element_season ({}) VALUES ({})".format(
                ", ".join(_SEASON_COLUMNS), ", ".join("?" * len(_SEASON_COLUMNS))),
            rows)
    return len(rows)


def _load_merged_gw(con, season, csv_text, archive_key) -> int:
    rows = []
    for r in csv.DictReader(io.StringIO(csv_text)):
        eid = as_int(r.get("element"))
        if eid is None or r.get("round") in (None, ""):
            continue
        # merged_gw uses 'True'/'False' strings for was_home
        home = _get(r, "was_home")
        r = dict(r)
        r["was_home"] = True if home == "True" else (False if home == "False" else None)
        rows.append(gw_row(season, NAME, eid, r, _get(r, "name"), archive_key))
    return insert_gw(con, rows)


def load(con: duckdb.DuckDBPyConnection, payload: dict, archive_key: str) -> int:
    season = payload["season"]
    data = payload["data"]
    rows = _load_players_raw(con, season, data.get("players_raw", ""), archive_key)
    rows += _load_merged_gw(con, season, data.get("merged_gw", ""), archive_key)
    return rows
