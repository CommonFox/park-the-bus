"""FotMob -- season stat-table leaderboards for the Premier League and Championship.

The endpoint `leagueseasondeepstats` returns one stat's leaderboard for one
season and type (players or teams). A full sweep is len(statsList) requests per
season per type. statsList is season-aware, so the sweep is driven off each
season's own statsList, discovered with a probe request. The season query
parameter is a numeric season id, itself discovered from the `seasons` array.

Ingest is incremental by (league, season, stat, type): a leaderboard already in
the archive is skipped unless `refetch`, so completed seasons are pulled once and
only the live season is refreshed.

One payload is one stat's leaderboard. The stat's title and category are read
from the payload's statsList by matching the stat name; the per-row value is
under statValue.value and the paired secondary under substatValue.value.
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
from .players import resolve_source_players

log = logging.getLogger(__name__)

NAME = "fotmob"

BASE = "https://www.fotmob.com/api/data/leagueseasondeepstats"
LEAGUES = {"premier-league": 47, "championship": 48}
STAT_TYPES = ("players", "teams")
PROBE_STAT = {"players": "goals", "teams": "rating_team"}
_POLITENESS_SECONDS = 0.5


def season_label(fotmob_name: str) -> str:
    """'2024/2025' -> '2024/25'."""
    start, end = fotmob_name.split("/")
    return "{}/{}".format(start, end[2:])


def _to_label(season) -> str:
    """Normalise a requested season to the warehouse label '2024/25'.

    Accepts an int start year (2024, as the CLI produces via parse_season), the
    slash form '2024/25', or the dash form '2024-25'."""
    if isinstance(season, int):
        return "{}/{:02d}".format(season, (season + 1) % 100)
    text = str(season)
    if "-" in text:
        start = int(text.split("-")[0])
        return "{}/{:02d}".format(start, (start + 1) % 100)
    return text


# --------------------------------------------------------------- ingest
# Network -> archive. Never touches the warehouse.

def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": config.USER_AGENT,
        "Accept": "application/json",
    })
    return session


def _get(session: requests.Session, params: Dict[str, Any], retries: int = 3) -> Dict[str, Any]:
    last: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            resp = session.get(BASE, params=params, timeout=config.REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as exc:
            last = exc
            if attempt < retries:
                time.sleep((2 ** attempt) + random.random())
    raise RuntimeError("failed to fetch fotmob {}: {}".format(params, last))


def _fetch_stat(session, league_id, season_id, stat, stat_type) -> Dict[str, Any]:
    return _get(session, {"id": league_id, "season": season_id, "stat": stat, "type": stat_type})


def _seasons(session, league_id) -> List[Dict[str, Any]]:
    """Discover the league's seasons (id + name) via a probe request.

    With no season parameter the endpoint still returns the league's full
    `seasons` array (confirmed live), which is the id-to-name map the sweep needs.
    """
    resp = _get(session, {"id": league_id, "stat": PROBE_STAT["players"], "type": "players"})
    return resp.get("seasons", [])


def _stats_list(session, league_id, season_id, stat_type) -> List[str]:
    """Discover a season's available stat names for a type."""
    resp = _fetch_stat(session, league_id, season_id, PROBE_STAT[stat_type], stat_type)
    return [s["name"] for s in resp.get("statsList", []) if s.get("name")]


def archived_stats(root: Optional[Path] = None) -> Set[str]:
    """Set of '{league}/{type}/{season}__{stat}' already in the archive."""
    done: Set[str] = set()
    for key in archive.keys(source=NAME, root=root):
        body = key[len("fotmob/"):]                # {league}/{type}/{label}__{stamp}.json.gz
        endpoint, _, name = body.rpartition("/")   # endpoint = {league}/{type}
        label = name.rsplit("__", 1)[0]            # {season}__{stat}
        done.add("{}/{}".format(endpoint, label))
    return done


def ingest(
    leagues: Optional[Sequence[str]] = None,
    seasons: Optional[Sequence[str]] = None,
    refetch: bool = False,
    captured_at: Optional[dt.datetime] = None,
    root: Optional[Path] = None,
    **_ignored,
) -> List[str]:
    leagues = list(leagues or LEAGUES)
    captured_at = captured_at or dt.datetime.utcnow().replace(microsecond=0)
    # Filter of season labels ('2024/25'); accepts int/slash/dash forms so the
    # CLI's --season (parsed to an int start year) works too.
    wanted = {_to_label(s) for s in seasons} if seasons else None

    unknown = [lg for lg in leagues if lg not in LEAGUES]
    if unknown:
        raise ValueError("unknown league(s): {}".format(", ".join(unknown)))

    session = _session()
    done = set() if refetch else archived_stats(root)
    written: List[str] = []

    for slug in leagues:
        league_id = LEAGUES[slug]
        for season in _seasons(session, league_id):
            label = season_label(season["name"])
            if wanted is not None and label not in wanted:
                continue
            season_id = season["id"]
            dash = label.replace("/", "-")
            for stat_type in STAT_TYPES:
                for stat in _stats_list(session, league_id, season_id, stat_type):
                    marker = "{}/{}/{}__{}".format(slug, stat_type, dash, stat)
                    if marker in done:
                        continue
                    data = _fetch_stat(session, league_id, season_id, stat, stat_type)
                    envelope = {
                        "source": NAME, "endpoint": "{}/{}".format(slug, stat_type),
                        "league": slug, "league_id": league_id, "season": label,
                        "season_id": season_id, "stat": stat, "stat_type": stat_type,
                        "url": BASE, "fetched_at": captured_at.isoformat() + "Z",
                        "data": data,
                    }
                    written.append(archive.write(
                        NAME, "{}/{}".format(slug, stat_type), envelope,
                        label="{}__{}".format(dash, stat), captured_at=captured_at,
                        root=root))
                    done.add(marker)
                    time.sleep(_POLITENESS_SECONDS)
            log.info("archived fotmob %s %s", slug, label)

    return written


# ----------------------------------------------------------------- load
# Archive -> src_fotmob_*. Never touches the network.

def _meta(payload: dict) -> tuple:
    """(stat_title, stat_category) for this payload's stat, from statsList."""
    stat = payload["stat"]
    for entry in payload["data"].get("statsList", []):
        if entry.get("name") == stat:
            return entry.get("title"), entry.get("category")
    return None, None


def _value(row: dict, key: str) -> Optional[float]:
    obj = row.get(key) or {}
    return as_float(obj.get("value"))


def _load_players(con, payload, archive_key) -> int:
    league_name = (payload["data"].get("leagueDetails") or {}).get("name")
    title, category = _meta(payload)
    rows = []
    for r in payload["data"].get("statsData", []):
        if r.get("id") is None:
            continue
        rows.append([
            payload["league_id"], league_name, payload["season"], payload.get("season_id"),
            payload["stat"], title, category, as_int(r.get("id")), as_int(r.get("teamId")),
            r.get("name"), as_int(r.get("position")), _value(r, "statValue"),
            _value(r, "substatValue"), as_int(r.get("rank")), archive_key,
        ])
    if rows:
        con.executemany(
            "INSERT OR REPLACE INTO src_fotmob_player_stat "
            "(league_id, league_name, season, season_id, stat_name, stat_title, stat_category, "
            " fotmob_player_id, fotmob_team_id, player_name, position_code, value, substat_value, "
            " rank, archive_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    return len(rows)


def _load_teams(con, payload, archive_key) -> int:
    league_name = (payload["data"].get("leagueDetails") or {}).get("name")
    title, category = _meta(payload)
    rows = []
    for r in payload["data"].get("statsData", []):
        if r.get("id") is None:
            continue
        rows.append([
            payload["league_id"], league_name, payload["season"], payload.get("season_id"),
            payload["stat"], title, category, as_int(r.get("id")), r.get("name"),
            _value(r, "statValue"), _value(r, "substatValue"), as_int(r.get("rank")), archive_key,
        ])
    if rows:
        con.executemany(
            "INSERT OR REPLACE INTO src_fotmob_team_stat "
            "(league_id, league_name, season, season_id, stat_name, stat_title, stat_category, "
            " fotmob_team_id, team_name, value, substat_value, rank, archive_key) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    return len(rows)


def load(con: duckdb.DuckDBPyConnection, payload: dict, archive_key: str) -> int:
    stat_type = payload.get("stat_type")
    if stat_type == "players":
        return _load_players(con, payload, archive_key)
    if stat_type == "teams":
        return _load_teams(con, payload, archive_key)
    log.warning("unknown fotmob stat_type %r in %s", stat_type, archive_key)
    return 0


# ------------------------------------------------------------- identity
# No resolve_matches: FotMob is scraped for season stat-tables, which carry
# no fixtures.

def resolve_players(con: duckdb.DuckDBPyConnection) -> int:
    """Match FotMob leaderboard players into dim_player. Idempotent.

    A player appears on every stat board for their league and season, so rows
    are collapsed to one per (player, season). Team names live on the team
    boards rather than the player rows, hence the join.
    """
    rows = con.execute(
        "SELECT p.fotmob_player_id, mode(p.player_name) AS player_name, "
        "       CASE p.league_id WHEN 47 THEN 'E0' WHEN 48 THEN 'E1' END AS competition, "
        "       p.season, mode(t.team_name) AS team_name "
        "FROM src_fotmob_player_stat p "
        "LEFT JOIN src_fotmob_team_stat t "
        "  ON t.league_id = p.league_id AND t.season = p.season "
        " AND t.fotmob_team_id = p.fotmob_team_id "
        "WHERE p.fotmob_player_id IS NOT NULL "
        "GROUP BY p.fotmob_player_id, p.league_id, p.season "
        "ORDER BY p.season, p.fotmob_player_id"
    ).fetchall()
    return resolve_source_players(con, NAME, rows)
