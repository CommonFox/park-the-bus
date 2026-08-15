"""ASA (American Soccer Analysis) -- games, xG and goals-added for the US leagues.

Free documented REST API at app.americansocceranalysis.com/api/v1/{league}/...
Reference resources (teams, players) carry no season and are re-fetched each run
(they are small). Seasonal resources (games and the per-season stat tables) are
incremental: a (league, resource, season) already archived is skipped unless
`refetch` is set.

Games and players are keyed by hashed ids, not names, so resolution later joins
games to the teams table for names.

Load routes on payload["resource"]: reference resources, per-game resources,
and the per-player-season stat tables each have their own shape. goals-added
nests a per-action-type array, exploded to one row per action; xgoals and
xpass are flat, one row per player-season.
"""
from __future__ import annotations

import datetime as dt
import logging
import random
import time
from pathlib import Path
from typing import Any, List, Optional, Sequence

import duckdb
import requests

from .. import archive, config
from .coerce import as_float, as_int
from .matches import resolve_match

log = logging.getLogger(__name__)

NAME = "asa"

BASE = "https://app.americansocceranalysis.com/api/v1"
LEAGUES = ("nwsl", "mls", "uslc", "usl1")
LEAGUE_TO_COMPETITION = {"nwsl": "NWSL", "mls": "MLS", "uslc": "USLC", "usl1": "USL1"}

REFERENCE_RESOURCES = ("teams", "players")
SEASONAL_RESOURCES = (
    "games", "games/xgoals",
    "players/goals-added", "players/xgoals", "players/xpass",
)
_POLITENESS_SECONDS = 0.5


def _resource_label(resource: str) -> str:
    """'games/xgoals' -> 'games-xgoals' for use as an archive endpoint segment."""
    return resource.replace("/", "-")


# --------------------------------------------------------------- ingest
# Network -> archive. Never touches the warehouse.

def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": config.USER_AGENT})
    return session


def _fetch(session: requests.Session, league: str, resource: str,
           season: Optional[str], retries: int = 3) -> Any:
    url = "{}/{}/{}".format(BASE, league, resource)
    params = {"season_name": season} if season else None
    last: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            resp = session.get(url, params=params, timeout=config.REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as exc:
            last = exc
            if attempt < retries:
                time.sleep((2 ** attempt) + random.random())
    raise RuntimeError("failed to fetch {} {}: {}".format(url, params, last))


def _seasonal_archived(league: str, resource: str, season: str,
                       root: Optional[Path] = None) -> bool:
    endpoint = "{}/{}".format(league, _resource_label(resource))
    prefix = "asa/{}/{}__".format(endpoint, season)
    return any(
        key.startswith(prefix)
        for key in archive.keys(source=NAME, endpoint=endpoint, root=root)
    )


def _archive_resource(league, resource, season, data, captured_at, root) -> str:
    endpoint = "{}/{}".format(league, _resource_label(resource))
    envelope = {
        "source": NAME, "endpoint": endpoint,
        "league": league, "resource": resource, "season": season,
        "url": "{}/{}/{}".format(BASE, league, resource),
        "fetched_at": captured_at.isoformat() + "Z", "data": data,
    }
    return archive.write(
        NAME, endpoint, envelope,
        label=season, captured_at=captured_at, root=root,
    )


def ingest(
    leagues: Optional[Sequence[str]] = None,
    seasons: Optional[Sequence[int]] = None,
    refetch: bool = False,
    captured_at: Optional[dt.datetime] = None,
    root: Optional[Path] = None,
    **_ignored,
) -> List[str]:
    leagues = list(leagues or LEAGUES)
    seasons = list(seasons or [dt.date.today().year])
    captured_at = captured_at or dt.datetime.utcnow().replace(microsecond=0)

    unknown = [lg for lg in leagues if lg not in LEAGUE_TO_COMPETITION]
    if unknown:
        raise ValueError("unknown league(s): {}".format(", ".join(unknown)))

    session = _session()
    written: List[str] = []

    for league in leagues:
        # Reference resources: no season, always refreshed (small).
        for resource in REFERENCE_RESOURCES:
            data = _fetch(session, league, resource, None)
            written.append(_archive_resource(
                league, resource, None, data, captured_at, root))
            time.sleep(_POLITENESS_SECONDS)

        for start_year in seasons:
            season = str(start_year)
            for resource in SEASONAL_RESOURCES:
                if not refetch and _seasonal_archived(league, resource, season, root):
                    continue
                data = _fetch(session, league, resource, season)
                written.append(_archive_resource(
                    league, resource, season, data, captured_at, root))
                time.sleep(_POLITENESS_SECONDS)
            log.info("archived asa %s %s", league, season)

    return written


# ----------------------------------------------------------------- load
# Archive -> src_asa_*. Never touches the network.

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
             as_int(r.get("home_score")), as_int(r.get("away_score")),
             as_int(r.get("matchday")), r.get("status"), archive_key]
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
             as_int(r.get("home_goals")), as_int(r.get("away_goals")),
             as_float(r.get("home_team_xgoals")), as_float(r.get("away_team_xgoals")),
             as_float(r.get("home_player_xgoals")), as_float(r.get("away_player_xgoals")),
             as_float(r.get("home_xpoints")), as_float(r.get("away_xpoints")), archive_key]
            for r in payload["data"] if r.get("game_id")]
    if rows:
        con.executemany(
            "INSERT OR REPLACE INTO src_asa_game_xgoals "
            "(game_id, league, season, home_team_id, away_team_id, home_goals, "
            " away_goals, home_team_xgoals, away_team_xgoals, home_player_xgoals, "
            " away_player_xgoals, home_xpoints, away_xpoints, archive_key) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    return len(rows)


def _load_goals_added(con, payload, archive_key) -> int:
    league, season = payload["league"], payload["season"]
    rows = []
    for r in payload["data"]:
        pid = r.get("player_id")
        if not pid:
            continue
        for action in r.get("data", []):
            rows.append([
                league, season, pid, r.get("team_id"), r.get("general_position"),
                as_int(r.get("minutes_played")), action.get("action_type"),
                as_float(action.get("goals_added_raw")),
                as_float(action.get("goals_added_above_avg")),
                as_int(action.get("count_actions")), archive_key,
            ])
    if rows:
        con.executemany(
            "INSERT OR REPLACE INTO src_asa_player_goals_added "
            "(league, season, player_id, team_id, general_position, minutes_played, "
            " action_type, goals_added_raw, goals_added_above_avg, count_actions, archive_key) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    return len(rows)


def _load_player_xgoals(con, payload, archive_key) -> int:
    league, season = payload["league"], payload["season"]
    rows = [[league, season, r["player_id"], r.get("team_id"), r.get("general_position"),
             as_int(r.get("minutes_played")), as_int(r.get("shots")),
             as_int(r.get("shots_on_target")),
             as_int(r.get("goals")), as_float(r.get("xgoals")), as_float(r.get("xplace")),
             as_int(r.get("key_passes")), as_int(r.get("primary_assists")),
             as_float(r.get("xassists")),
             as_int(r.get("goals_plus_primary_assists")),
             as_float(r.get("xgoals_plus_xassists")),
             as_float(r.get("points_added")), as_float(r.get("xpoints_added")), archive_key]
            for r in payload["data"] if r.get("player_id")]
    if rows:
        con.executemany(
            "INSERT OR REPLACE INTO src_asa_player_xgoals "
            "(league, season, player_id, team_id, general_position, minutes_played, shots, "
            " shots_on_target, goals, xgoals, xplace, key_passes, primary_assists, xassists, "
            " goals_plus_primary_assists, xgoals_plus_xassists, points_added, xpoints_added, "
            " archive_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    return len(rows)


def _load_xpass(con, payload, archive_key) -> int:
    league, season = payload["league"], payload["season"]
    rows = [[league, season, r["player_id"], r.get("team_id"), r.get("general_position"),
             as_int(r.get("minutes_played")), as_int(r.get("attempted_passes")),
             as_float(r.get("pass_completion_percentage")),
             as_float(r.get("xpass_completion_percentage")),
             as_float(r.get("passes_completed_over_expected")),
             as_float(r.get("passes_completed_over_expected_p100")),
             as_float(r.get("avg_distance_yds")), as_float(r.get("avg_vertical_distance_yds")),
             as_float(r.get("share_team_touches")), as_int(r.get("count_games")), archive_key]
            for r in payload["data"] if r.get("player_id")]
    if rows:
        con.executemany(
            "INSERT OR REPLACE INTO src_asa_player_xpass "
            "(league, season, player_id, team_id, general_position, minutes_played, "
            " attempted_passes, pass_completion_percentage, xpass_completion_percentage, "
            " passes_completed_over_expected, passes_completed_over_expected_p100, "
            " avg_distance_yds, avg_vertical_distance_yds, share_team_touches, count_games, "
            " archive_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    return len(rows)


_ROUTES = {
    "teams": _load_teams,
    "players": _load_players,
    "games": _load_games,
    "games/xgoals": _load_game_xgoals,
    "players/goals-added": _load_goals_added,
    "players/xgoals": _load_player_xgoals,
    "players/xpass": _load_xpass,
}


def load(con: duckdb.DuckDBPyConnection, payload: dict, archive_key: str) -> int:
    resource = payload.get("resource")
    handler = _ROUTES.get(resource)
    if handler is None:
        log.warning("no asa loader for resource %r in %s", resource, archive_key)
        return 0
    return handler(con, payload, archive_key)


# ------------------------------------------------------------- identity

def resolve_matches(con: duckdb.DuckDBPyConnection) -> int:
    """Resolve every ASA game into dim_match. Idempotent.

    ASA games reference teams by hashed id, so the game is joined to
    src_asa_team for the names resolve_match needs. NWSL kickoffs are true UTC,
    so late kickoffs crossing the local date boundary resolve through the
    +/-36h window against any other source reporting the local date.
    """
    rows = con.execute(
        "SELECT g.game_id, g.league, g.season, g.kickoff_utc, ht.team_name, awt.team_name "
        "FROM src_asa_game g "
        "JOIN src_asa_team ht ON ht.league = g.league AND ht.team_id = g.home_team_id "
        "JOIN src_asa_team awt ON awt.league = g.league AND awt.team_id = g.away_team_id "
        "WHERE g.kickoff_utc IS NOT NULL "
        "ORDER BY g.kickoff_utc, g.game_id"
    ).fetchall()

    resolved = 0
    for game_id, league, season, kickoff, home, away in rows:
        competition = LEAGUE_TO_COMPETITION.get(league)
        if competition is None:
            continue
        if resolve_match(
            con, source=NAME, source_match_id=str(game_id),
            competition=competition, season=season, kickoff=kickoff,
            home_team=home, away_team=away,
        ) is not None:
            resolved += 1
    return resolved
