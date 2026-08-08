"""FPL API payloads -> src_fpl_* tables, routed on payload["endpoint"].

- "bootstrap" -> src_fpl_team / src_fpl_position / src_fpl_event / src_fpl_element
- "fixtures"  -> src_fpl_fixture            (Task 3)
- "element"   -> src_fpl_player_gw          (Task 4)

FPL sends percentages and expected stats as strings; each numeric is coerced
defensively.
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


def _ts(value: Any) -> Optional[dt.datetime]:
    if not value:
        return None
    text = str(value).replace("Z", "").strip()
    try:
        return dt.datetime.fromisoformat(text)
    except ValueError:
        return None


def _date(value: Any) -> Optional[dt.date]:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        return None


def _load_bootstrap(con, payload, archive_key) -> int:
    season = payload["season"]
    data = payload["data"]

    teams = [[season, t["id"], t.get("name"), t.get("short_name"), _i(t.get("strength")),
              _i(t.get("strength_overall_home")), _i(t.get("strength_overall_away")),
              _i(t.get("strength_attack_home")), _i(t.get("strength_attack_away")),
              _i(t.get("strength_defence_home")), _i(t.get("strength_defence_away")),
              archive_key] for t in data.get("teams", []) if t.get("id") is not None]
    if teams:
        con.executemany(
            "INSERT OR REPLACE INTO src_fpl_team (season, team_id, name, short_name, "
            "strength, strength_overall_home, strength_overall_away, strength_attack_home, "
            "strength_attack_away, strength_defence_home, strength_defence_away, archive_key) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", teams)

    positions = [[p["id"], p.get("singular_name"), p.get("singular_name_short")]
                 for p in data.get("element_types", []) if p.get("id") is not None]
    if positions:
        con.executemany(
            "INSERT OR REPLACE INTO src_fpl_position (position_id, singular_name, short_name) "
            "VALUES (?, ?, ?)", positions)

    events = [[season, e["id"], e.get("name"), _ts(e.get("deadline_time")),
               e.get("finished"), e.get("is_current"), e.get("is_next"),
               _i(e.get("average_entry_score")), _i(e.get("highest_score")), archive_key]
              for e in data.get("events", []) if e.get("id") is not None]
    if events:
        con.executemany(
            "INSERT OR REPLACE INTO src_fpl_event (season, event_id, name, deadline_time, "
            "finished, is_current, is_next, average_entry_score, highest_score, archive_key) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", events)

    elements = [[season, e["id"], _i(e.get("code")), e.get("web_name"), e.get("first_name"),
                 e.get("second_name"), _i(e.get("team")), _i(e.get("element_type")),
                 _i(e.get("now_cost")), _i(e.get("total_points")), _f(e.get("form")),
                 _f(e.get("selected_by_percent")), e.get("status"), _i(e.get("minutes")),
                 _i(e.get("goals_scored")), _i(e.get("assists")), _i(e.get("clean_sheets")),
                 _i(e.get("bonus")), _i(e.get("bps")), _f(e.get("expected_goals")),
                 _f(e.get("expected_assists")), _f(e.get("expected_goal_involvements")),
                 _date(e.get("birth_date")), e.get("opta_code"),
                 archive_key] for e in data.get("elements", []) if e.get("id") is not None]
    if elements:
        con.executemany(
            "INSERT OR REPLACE INTO src_fpl_element (season, element_id, code, web_name, "
            "first_name, second_name, team, element_type, now_cost, total_points, form, "
            "selected_by_percent, status, minutes, goals_scored, assists, clean_sheets, "
            "bonus, bps, expected_goals, expected_assists, expected_goal_involvements, "
            "birth_date, opta_code, "
            "archive_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "?, ?, ?, ?, ?, ?, ?)", elements)

    return len(teams) + len(positions) + len(events) + len(elements)


def load_fpl(con: duckdb.DuckDBPyConnection, payload: dict, archive_key: str) -> int:
    endpoint = payload.get("endpoint")
    if endpoint == "bootstrap":
        return _load_bootstrap(con, payload, archive_key)
    if endpoint == "fixtures":
        from .fpl_fixtures import load_fixtures
        return load_fixtures(con, payload, archive_key)
    if endpoint == "element":
        from .fpl_fixtures import load_element_history
        return load_element_history(con, payload, archive_key)
    log.warning("unknown fpl endpoint %r in %s", endpoint, archive_key)
    return 0


LOADERS["fpl"] = load_fpl
