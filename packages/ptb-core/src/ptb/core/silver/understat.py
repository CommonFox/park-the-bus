"""Understat -- shot-level xG for the Big 5 men's leagues.

Understat was reworked to a JS shell in late 2025; data comes from plain JSON
endpoints, not embedded HTML. `getLeagueData/{league}/{year}` returns
{teams, players, dates}; `getMatchData/{id}` returns {shots: {h, a}, ...}.

Ingest is two-phase: one league payload per league-season (giving the match
list and match-level xG), then one shot payload per played match. The match
phase is incremental -- a match already in the archive is skipped unless
`refetch` is set -- because a full shot sweep is ~2000 requests per season.

Load routes on payload["endpoint"]:
- "league" -> one row per dated match (match-level xG), plus one row per
  league-season roster entry (season-aggregate stats, every squad player)
- "match"  -> one row per shot

Understat sends every numeric value as a string, so each is coerced
defensively (see coerce.py) and a stray empty string never aborts a rebuild.
"""
from __future__ import annotations

import datetime as dt
import logging
import random
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set

import duckdb
import requests

from .. import archive, config
from .coerce import as_float, as_int
from .matches import resolve_match
from .players import resolve_source_players

log = logging.getLogger(__name__)

NAME = "understat"

BASE = "https://understat.com"
LEAGUES = ("EPL", "La_liga", "Bundesliga", "Serie_A", "Ligue_1")
LEAGUE_TO_COMPETITION = {
    "EPL": "E0", "La_liga": "SP1", "Bundesliga": "D1", "Serie_A": "I1", "Ligue_1": "F1",
}
_POLITENESS_SECONDS = 1.0


def season_label(start_year: int) -> str:
    """2024 -> '2024/25'."""
    return "{}/{:02d}".format(start_year, (start_year + 1) % 100)


def parse_season(label: str) -> int:
    """'2024/25' -> 2024."""
    return int(label.split("/")[0])


# --------------------------------------------------------------- ingest
# Network -> archive. Never touches the warehouse.

def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": config.USER_AGENT,
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
    })
    return session


def _get_json(session: requests.Session, url: str, referer: str, retries: int = 3) -> Any:
    last: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            resp = session.get(url, headers={"Referer": referer}, timeout=config.REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as exc:
            last = exc
            if attempt < retries:
                time.sleep((2 ** attempt) + random.random())
    raise RuntimeError("failed to fetch {}: {}".format(url, last))


def _fetch_league(session: requests.Session, league: str, start_year: int) -> Dict[str, Any]:
    url = "{}/getLeagueData/{}/{}".format(BASE, league, start_year)
    return _get_json(session, url, "{}/league/{}/{}".format(BASE, league, start_year))


def _fetch_match(session: requests.Session, match_id: str) -> Dict[str, Any]:
    url = "{}/getMatchData/{}".format(BASE, match_id)
    return _get_json(session, url, "{}/match/{}".format(BASE, match_id))


def archived_match_ids(root: Optional[Path] = None) -> Set[str]:
    """Understat match ids already present in the archive."""
    prefix = "understat/match/"
    ids: Set[str] = set()
    for key in archive.keys(source=NAME, endpoint="match", root=root):
        name = key[len(prefix):]
        ids.add(name.split("__", 1)[0])
    return ids


def ingest(
    leagues: Optional[Sequence[str]] = None,
    seasons: Optional[Sequence[int]] = None,
    refetch: bool = False,
    captured_at: Optional[dt.datetime] = None,
    root: Optional[Path] = None,
    **_ignored,
) -> List[str]:
    leagues = list(leagues or LEAGUES)
    seasons = list(seasons or [dt.date.today().year - (0 if dt.date.today().month >= 7 else 1)])
    captured_at = captured_at or dt.datetime.utcnow().replace(microsecond=0)

    unknown = [lg for lg in leagues if lg not in LEAGUE_TO_COMPETITION]
    if unknown:
        raise ValueError("unknown league(s): {}".format(", ".join(unknown)))

    session = _session()
    seen = set() if refetch else archived_match_ids(root)
    written: List[str] = []

    for start_year in seasons:
        season = season_label(start_year)
        for league in leagues:
            data = _fetch_league(session, league, start_year)
            url = "{}/getLeagueData/{}/{}".format(BASE, league, start_year)
            envelope = {
                "source": NAME, "endpoint": "league",
                "league": league, "competition": LEAGUE_TO_COMPETITION[league],
                "season": season, "url": url,
                "fetched_at": captured_at.isoformat() + "Z", "data": data,
            }
            written.append(archive.write(
                NAME, "league", envelope,
                label="{}__{}".format(league, season.replace("/", "-")),
                captured_at=captured_at, root=root,
            ))
            log.info("archived understat league %s %s", league, season)
            time.sleep(_POLITENESS_SECONDS)

            match_ids = [str(d["id"]) for d in data.get("dates", []) if d.get("isResult")]
            for match_id in match_ids:
                if match_id in seen:
                    continue
                shot_data = _fetch_match(session, match_id)
                match_env = {
                    "source": NAME, "endpoint": "match",
                    "understat_match_id": match_id,
                    "url": "{}/getMatchData/{}".format(BASE, match_id),
                    "fetched_at": captured_at.isoformat() + "Z", "data": shot_data,
                }
                written.append(archive.write(
                    NAME, "match", match_env,
                    label=match_id, captured_at=captured_at, root=root,
                ))
                seen.add(match_id)
                time.sleep(_POLITENESS_SECONDS)
            log.info("archived understat shots %s %s (%d matches)", league, season, len(match_ids))

    return written


# ----------------------------------------------------------------- load
# Archive -> src_understat_*. Never touches the network.

_MATCH_COLUMNS = [
    "understat_match_id", "competition", "season", "kickoff", "home_team", "away_team",
    "home_goals", "away_goals", "home_xg", "away_xg", "forecast_w", "forecast_d",
    "forecast_l", "archive_key",
]

_PLAYER_COLUMNS = [
    "understat_player_id", "competition", "season", "player_name", "team_name",
    "position", "games", "minutes", "goals", "non_penalty_goals", "assists",
    "shots", "key_passes", "xg", "npxg", "xa", "xg_buildup", "xg_chain",
    "yellow_cards", "red_cards", "archive_key",
]

_SHOT_COLUMNS = [
    "understat_shot_id", "understat_match_id", "minute", "player", "player_id",
    "team", "home_away", "xg", "result", "situation", "shot_type", "x", "y",
    "assist_player", "last_action", "archive_key",
]


def _datetime(value: Any) -> Optional[dt.datetime]:
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
            str(entry["id"]), competition, season, _datetime(entry.get("datetime")),
            home, away, as_int(goals.get("h")), as_int(goals.get("a")),
            as_float(xg.get("h")), as_float(xg.get("a")),
            as_float(forecast.get("w")), as_float(forecast.get("d")),
            as_float(forecast.get("l")),
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


def _load_league_players(con: duckdb.DuckDBPyConnection, payload: dict, archive_key: str) -> int:
    competition = payload["competition"]
    season = payload["season"]
    rows = []
    for entry in payload["data"].get("players", []):
        player_id = entry.get("id")
        if not player_id:
            continue
        rows.append([
            str(player_id), competition, season,
            entry.get("player_name"), entry.get("team_title"), entry.get("position"),
            as_int(entry.get("games")), as_int(entry.get("time")),
            as_int(entry.get("goals")), as_int(entry.get("npg")), as_int(entry.get("assists")),
            as_int(entry.get("shots")), as_int(entry.get("key_passes")),
            as_float(entry.get("xG")), as_float(entry.get("npxG")), as_float(entry.get("xA")),
            as_float(entry.get("xGBuildup")), as_float(entry.get("xGChain")),
            as_int(entry.get("yellow_cards")), as_int(entry.get("red_cards")),
            archive_key,
        ])
    if rows:
        con.executemany(
            "INSERT OR REPLACE INTO src_understat_player ({}) VALUES ({})".format(
                ", ".join(_PLAYER_COLUMNS), ", ".join("?" * len(_PLAYER_COLUMNS))
            ),
            rows,
        )
    return len(rows)


def _load_match_shots(con: duckdb.DuckDBPyConnection, payload: dict, archive_key: str) -> int:
    """Each shot carries h_team and a_team plus h_a; the shooting team is
    selected by h_a so the row records which club actually took the shot."""
    match_id = str(payload["understat_match_id"])
    shots = (payload.get("data") or {}).get("shots") or {}
    rows = []
    for side in ("h", "a"):
        for shot in shots.get(side, []):
            team = shot.get("h_team") if side == "h" else shot.get("a_team")
            rows.append([
                str(shot["id"]), match_id, as_int(shot.get("minute")),
                shot.get("player"), shot.get("player_id"), team, side,
                as_float(shot.get("xG")), shot.get("result"), shot.get("situation"),
                shot.get("shotType"), as_float(shot.get("X")), as_float(shot.get("Y")),
                shot.get("player_assisted"), shot.get("lastAction"), archive_key,
            ])
    if rows:
        con.executemany(
            "INSERT OR REPLACE INTO src_understat_shot ({}) VALUES ({})".format(
                ", ".join(_SHOT_COLUMNS), ", ".join("?" * len(_SHOT_COLUMNS))
            ),
            rows,
        )
    return len(rows)


def load(con: duckdb.DuckDBPyConnection, payload: dict, archive_key: str) -> int:
    endpoint = payload.get("endpoint")
    if endpoint == "league":
        return (_load_league(con, payload, archive_key)
                + _load_league_players(con, payload, archive_key))
    if endpoint == "match":
        return _load_match_shots(con, payload, archive_key)
    log.warning("unknown understat endpoint %r in %s", endpoint, archive_key)
    return 0


# ------------------------------------------------------------- identity

def resolve_matches(con: duckdb.DuckDBPyConnection) -> int:
    """Resolve every understat match into dim_match. Idempotent.

    Big-5 matches resolve against football-data's existing dim_match rows via
    the +/-36h window, which is the first cross-source match identity in the
    project.
    """
    rows = con.execute(
        "SELECT understat_match_id, competition, season, kickoff, home_team, away_team "
        "FROM src_understat_match WHERE kickoff IS NOT NULL "
        "ORDER BY kickoff, understat_match_id"
    ).fetchall()

    resolved = 0
    for match_id, competition, season, kickoff, home, away in rows:
        if resolve_match(
            con, source=NAME, source_match_id=str(match_id),
            competition=competition, season=season, kickoff=kickoff,
            home_team=home, away_team=away,
        ) is not None:
            resolved += 1
    return resolved


def resolve_players(con: duckdb.DuckDBPyConnection) -> int:
    """Match Understat players into dim_player. Idempotent.

    src_understat_shot alone only surfaces shot-takers -- most goalkeepers and
    many fringe subs never appear there and would never enter identity
    resolution at all. src_understat_player is Understat's own league-season
    roster, which covers every squad player including zero-shot ones; it is
    full-outer-joined against the shot-derived rows per (player, competition,
    season) block so both contribute candidates.

    Where a block has shot data, its name/team wins (a player can move club
    mid-season, so the modal team across their shots is used rather than any
    single shot's or the roster row's season-aggregate club). The roster row
    only fills in where no shot was ever recorded for that block.
    """
    rows = con.execute(
        "WITH shots AS ("
        "  SELECT s.player_id AS understat_player_id, m.competition, m.season, "
        "         mode(s.player) AS player_name, mode(s.team) AS team_name "
        "  FROM src_understat_shot s "
        "  JOIN src_understat_match m ON m.understat_match_id = s.understat_match_id "
        "  WHERE s.player_id IS NOT NULL "
        "  GROUP BY s.player_id, m.competition, m.season "
        ") "
        "SELECT "
        "  coalesce(shots.understat_player_id, roster.understat_player_id), "
        "  coalesce(shots.player_name, roster.player_name), "
        "  coalesce(shots.competition, roster.competition), "
        "  coalesce(shots.season, roster.season), "
        "  coalesce(shots.team_name, roster.team_name) "
        "FROM shots "
        "FULL OUTER JOIN src_understat_player roster "
        "  ON roster.understat_player_id = shots.understat_player_id "
        " AND roster.competition = shots.competition "
        " AND roster.season = shots.season "
        "ORDER BY 4, 1"
    ).fetchall()
    return resolve_source_players(con, NAME, rows)
