"""vaastav season payload -> src_fpl_element_season and src_fpl_player_gw.

Two CSVs share one envelope. players_raw is the per-season player snapshot;
merged_gw is one row per player per gameweek, reusing the same row builder as
the FPL API element-summary loader so both sources land in src_fpl_player_gw.
Columns vary by era, so every field is read by name and tolerated when absent.
"""
from __future__ import annotations

import csv
import io
from typing import Any, Optional

import duckdb

from ..load import LOADERS
from .fpl_fixtures import _gw_row, _insert_gw


def _i(value: Any) -> Optional[int]:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _get(row: dict, key: str) -> Optional[str]:
    value = row.get(key)
    if value is None:
        return None
    value = str(value).strip()
    return value or None


_SEASON_COLUMNS = [
    "season", "element_id", "code", "first_name", "second_name", "web_name",
    "element_type", "team", "team_code", "now_cost", "total_points", "minutes",
    "archive_key",
]


def _load_players_raw(con, season, csv_text, archive_key) -> int:
    rows = []
    for r in csv.DictReader(io.StringIO(csv_text)):
        eid = _i(r.get("id"))
        if eid is None:
            continue
        rows.append([
            season, eid, _i(r.get("code")), _get(r, "first_name"), _get(r, "second_name"),
            _get(r, "web_name"), _i(r.get("element_type")), _i(r.get("team")),
            _i(r.get("team_code")), _i(r.get("now_cost")), _i(r.get("total_points")),
            _i(r.get("minutes")), archive_key,
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
        eid = _i(r.get("element"))
        if eid is None or r.get("round") in (None, ""):
            continue
        # merged_gw uses 'True'/'False' strings for was_home
        home = _get(r, "was_home")
        r = dict(r)
        r["was_home"] = True if home == "True" else (False if home == "False" else None)
        rows.append(_gw_row(season, "vaastav", eid, r, _get(r, "name"), archive_key))
    return _insert_gw(con, rows)


def load_vaastav(con: duckdb.DuckDBPyConnection, payload: dict, archive_key: str) -> int:
    season = payload["season"]
    data = payload["data"]
    n = _load_players_raw(con, season, data.get("players_raw", ""), archive_key)
    n += _load_merged_gw(con, season, data.get("merged_gw", ""), archive_key)
    return n


LOADERS["vaastav"] = load_vaastav
