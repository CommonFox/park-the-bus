"""FPL API -- the official Fantasy Premier League public endpoints.

Three payloads per run: one bootstrap-static (players, teams, events,
positions), one fixtures list, and one element-summary per player. The API
serves only the current season and its data mutates every gameweek, so every
run fetches a fresh, timestamped snapshot -- the archive is append-only, and a
rebuild simply loads the latest snapshot per key. There is no incremental skip;
skipping already-archived players would miss each new gameweek's history.

Load routes on payload["endpoint"]:
- "bootstrap" -> src_fpl_team / src_fpl_position / src_fpl_event / src_fpl_element
- "fixtures"  -> src_fpl_fixture
- "element"   -> src_fpl_player_gw

`gw_row` and `insert_gw` are shared with vaastav.py, which backfills the same
per-gameweek shape for historical seasons this API no longer serves.

FPL sends percentages and expected stats as strings; each numeric is coerced
defensively (see coerce.py).
"""
from __future__ import annotations

import datetime as dt
import logging
import random
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import duckdb
import requests

from .. import archive, config
from .coerce import as_float, as_int
from .matches import resolve_match

log = logging.getLogger(__name__)

NAME = "fpl"

BASE = "https://fantasy.premierleague.com/api"
_POLITENESS_SECONDS = 0.3


def season_from_events(events: List[Dict[str, Any]]) -> str:
    """Derive '2024/25' from the earliest event deadline year."""
    years = [int(e["deadline_time"][:4]) for e in events if e.get("deadline_time")]
    start = min(years) if years else dt.date.today().year
    return "{}/{:02d}".format(start, (start + 1) % 100)


# --------------------------------------------------------------- ingest
# Network -> archive. Never touches the warehouse.

def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": config.USER_AGENT})
    return session


def _get_json(session: requests.Session, path: str, retries: int = 3) -> Any:
    url = "{}/{}".format(BASE, path)
    last: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            resp = session.get(url, timeout=config.REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as exc:
            last = exc
            if attempt < retries:
                time.sleep((2 ** attempt) + random.random())
    raise RuntimeError("failed to fetch {}: {}".format(url, last))


def _fetch_bootstrap(session: requests.Session) -> Dict[str, Any]:
    return _get_json(session, "bootstrap-static/")


def _fetch_fixtures(session: requests.Session) -> List[Dict[str, Any]]:
    return _get_json(session, "fixtures/")


def _fetch_element(session: requests.Session, element_id: int) -> Dict[str, Any]:
    return _get_json(session, "element-summary/{}/".format(element_id))


def ingest(
    captured_at: Optional[dt.datetime] = None,
    root: Optional[Path] = None,
    **_ignored,
) -> List[str]:
    captured_at = captured_at or dt.datetime.utcnow().replace(microsecond=0)
    session = _session()

    bootstrap = _fetch_bootstrap(session)
    season = season_from_events(bootstrap.get("events", []))
    label = season.replace("/", "-")
    stamp = captured_at.isoformat() + "Z"
    written: List[str] = []

    written.append(archive.write(
        NAME, "bootstrap",
        {"source": NAME, "endpoint": "bootstrap", "season": season,
         "url": "{}/bootstrap-static/".format(BASE), "fetched_at": stamp,
         "data": bootstrap},
        label=label, captured_at=captured_at, root=root))

    fixtures = _fetch_fixtures(session)
    written.append(archive.write(
        NAME, "fixtures",
        {"source": NAME, "endpoint": "fixtures", "season": season,
         "url": "{}/fixtures/".format(BASE), "fetched_at": stamp,
         "data": fixtures},
        label=label, captured_at=captured_at, root=root))
    time.sleep(_POLITENESS_SECONDS)

    for element in bootstrap.get("elements", []):
        eid = element["id"]
        data = _fetch_element(session, eid)
        written.append(archive.write(
            NAME, "element",
            {"source": NAME, "endpoint": "element", "season": season,
             "element_id": eid, "url": "{}/element-summary/{}/".format(BASE, eid),
             "fetched_at": stamp, "data": data},
            label=str(eid), captured_at=captured_at, root=root))
        time.sleep(_POLITENESS_SECONDS)

    log.info("archived fpl %s: bootstrap + fixtures + %d elements",
             season, len(bootstrap.get("elements", [])))
    return written


# ----------------------------------------------------------------- load
# Archive -> src_fpl_*. Never touches the network.

_FIXTURE_COLUMNS = [
    "season", "fixture_id", "code", "event", "kickoff_time", "team_h", "team_a",
    "team_h_score", "team_a_score", "finished", "team_h_difficulty",
    "team_a_difficulty", "archive_key",
]

_GW_COLUMNS = [
    "season", "source", "element_id", "event", "fixture", "opponent_team", "minutes",
    "total_points", "goals_scored", "assists", "clean_sheets", "goals_conceded",
    "own_goals", "penalties_saved", "penalties_missed", "yellow_cards", "red_cards",
    "saves", "bonus", "bps", "influence", "creativity", "threat", "ict_index",
    "expected_goals", "expected_assists", "expected_goal_involvements",
    "expected_goals_conceded", "value", "selected", "transfers_balance",
    "transfers_in", "transfers_out", "was_home", "kickoff_time", "team_h_score",
    "team_a_score", "starts", "player_name", "archive_key",
]


def _timestamp(value: Any) -> Optional[dt.datetime]:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "").strip())
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

    teams = [[season, t["id"], t.get("name"), t.get("short_name"), as_int(t.get("strength")),
              as_int(t.get("strength_overall_home")), as_int(t.get("strength_overall_away")),
              as_int(t.get("strength_attack_home")), as_int(t.get("strength_attack_away")),
              as_int(t.get("strength_defence_home")), as_int(t.get("strength_defence_away")),
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

    events = [[season, e["id"], e.get("name"), _timestamp(e.get("deadline_time")),
               e.get("finished"), e.get("is_current"), e.get("is_next"),
               as_int(e.get("average_entry_score")), as_int(e.get("highest_score")), archive_key]
              for e in data.get("events", []) if e.get("id") is not None]
    if events:
        con.executemany(
            "INSERT OR REPLACE INTO src_fpl_event (season, event_id, name, deadline_time, "
            "finished, is_current, is_next, average_entry_score, highest_score, archive_key) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", events)

    elements = [[season, e["id"], as_int(e.get("code")), e.get("web_name"), e.get("first_name"),
                 e.get("second_name"), as_int(e.get("team")), as_int(e.get("element_type")),
                 as_int(e.get("now_cost")), as_int(e.get("total_points")), as_float(e.get("form")),
                 as_float(e.get("selected_by_percent")), e.get("status"), as_int(e.get("minutes")),
                 as_int(e.get("goals_scored")), as_int(e.get("assists")),
                 as_int(e.get("clean_sheets")),
                 as_int(e.get("bonus")), as_int(e.get("bps")), as_float(e.get("expected_goals")),
                 as_float(e.get("expected_assists")),
                 as_float(e.get("expected_goal_involvements")),
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


def _load_fixtures(con, payload, archive_key) -> int:
    season = payload["season"]
    rows = []
    for fx in payload["data"]:
        if fx.get("id") is None:
            continue
        rows.append([
            season, as_int(fx.get("id")), as_int(fx.get("code")), as_int(fx.get("event")),
            _timestamp(fx.get("kickoff_time")), as_int(fx.get("team_h")),
            as_int(fx.get("team_a")),
            as_int(fx.get("team_h_score")), as_int(fx.get("team_a_score")), fx.get("finished"),
            as_int(fx.get("team_h_difficulty")), as_int(fx.get("team_a_difficulty")),
            archive_key,
        ])
    if rows:
        con.executemany(
            "INSERT OR REPLACE INTO src_fpl_fixture ({}) VALUES ({})".format(
                ", ".join(_FIXTURE_COLUMNS), ", ".join("?" * len(_FIXTURE_COLUMNS))),
            rows)
    return len(rows)


def gw_row(season: str, source: str, element_id: Optional[int], row: dict,
           player_name: Optional[str], archive_key: str) -> list:
    """One gameweek dict -> one src_fpl_player_gw row. Shared with vaastav.py."""
    return [
        season, source, element_id, as_int(row.get("round")), as_int(row.get("fixture")),
        as_int(row.get("opponent_team")), as_int(row.get("minutes")),
        as_int(row.get("total_points")),
        as_int(row.get("goals_scored")), as_int(row.get("assists")),
        as_int(row.get("clean_sheets")),
        as_int(row.get("goals_conceded")), as_int(row.get("own_goals")),
        as_int(row.get("penalties_saved")),
        as_int(row.get("penalties_missed")), as_int(row.get("yellow_cards")),
        as_int(row.get("red_cards")),
        as_int(row.get("saves")), as_int(row.get("bonus")), as_int(row.get("bps")),
        as_float(row.get("influence")),
        as_float(row.get("creativity")), as_float(row.get("threat")),
        as_float(row.get("ict_index")),
        as_float(row.get("expected_goals")), as_float(row.get("expected_assists")),
        as_float(row.get("expected_goal_involvements")),
        as_float(row.get("expected_goals_conceded")),
        as_int(row.get("value")), as_int(row.get("selected")),
        as_int(row.get("transfers_balance")),
        as_int(row.get("transfers_in")), as_int(row.get("transfers_out")), row.get("was_home"),
        _timestamp(row.get("kickoff_time")), as_int(row.get("team_h_score")),
        as_int(row.get("team_a_score")),
        as_int(row.get("starts")), player_name, archive_key,
    ]


def insert_gw(con: duckdb.DuckDBPyConnection, rows: List[list]) -> int:
    """Shared with vaastav.py -- both sources land in src_fpl_player_gw."""
    if rows:
        con.executemany(
            "INSERT OR REPLACE INTO src_fpl_player_gw ({}) VALUES ({})".format(
                ", ".join(_GW_COLUMNS), ", ".join("?" * len(_GW_COLUMNS))),
            rows)
    return len(rows)


def _load_element_history(con, payload, archive_key) -> int:
    season = payload["season"]
    element_id = as_int(payload.get("element_id"))
    history = (payload.get("data") or {}).get("history", [])
    rows = [gw_row(season, "api", element_id, h, None, archive_key) for h in history]
    return insert_gw(con, rows)


def load(con: duckdb.DuckDBPyConnection, payload: dict, archive_key: str) -> int:
    endpoint = payload.get("endpoint")
    if endpoint == "bootstrap":
        return _load_bootstrap(con, payload, archive_key)
    if endpoint == "fixtures":
        return _load_fixtures(con, payload, archive_key)
    if endpoint == "element":
        return _load_element_history(con, payload, archive_key)
    log.warning("unknown fpl endpoint %r in %s", endpoint, archive_key)
    return 0


# ------------------------------------------------------------- identity
# No resolve_players here: FPL is the spine every other source is matched
# into, built by players.build_fpl_spine before any source resolves.

def resolve_matches(con: duckdb.DuckDBPyConnection) -> int:
    """Resolve every FPL fixture into dim_match. Idempotent.

    FPL fixtures reference teams by numeric id, so each is joined to
    src_fpl_team for the names resolve_match needs. Fixtures are Premier League
    matches, so they resolve against football-data and Understat rows already in
    dim_match -- the first three-source match identity in the project.
    """
    rows = con.execute(
        "SELECT f.season, f.fixture_id, f.kickoff_time, th.name, ta.name "
        "FROM src_fpl_fixture f "
        "JOIN src_fpl_team th ON th.season = f.season AND th.team_id = f.team_h "
        "JOIN src_fpl_team ta ON ta.season = f.season AND ta.team_id = f.team_a "
        "WHERE f.kickoff_time IS NOT NULL "
        "ORDER BY f.kickoff_time, f.fixture_id"
    ).fetchall()

    resolved = 0
    for season, fixture_id, kickoff, home, away in rows:
        if resolve_match(
            con, source=NAME, source_match_id="{}|{}".format(season, fixture_id),
            competition="E0", season=season, kickoff=kickoff,
            home_team=home, away_team=away,
        ) is not None:
            resolved += 1
    return resolved
