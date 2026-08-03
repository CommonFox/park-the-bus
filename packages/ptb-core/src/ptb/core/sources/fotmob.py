"""FotMob -- season stat-table leaderboards for the Premier League and Championship.

The endpoint `leagueseasondeepstats` returns one stat's leaderboard for one
season and type (players or teams). A full sweep is len(statsList) requests per
season per type. statsList is season-aware, so the sweep is driven off each
season's own statsList, discovered with a probe request. The season query
parameter is a numeric season id, itself discovered from the `seasons` array.

Ingest is incremental by (league, season, stat, type): a leaderboard already in
the archive is skipped unless `refetch`, so completed seasons are pulled once and
only the live season is refreshed.
"""
from __future__ import annotations

import datetime as dt
import logging
import random
import time
from typing import Any, Dict, List, Optional, Sequence, Set

import requests

from .. import config
from ..archive import RawArchive
from .registry import register_source

log = logging.getLogger(__name__)

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


def archived_stats(archive: RawArchive) -> Set[str]:
    """Set of '{league}/{type}/{season}__{stat}' already in the archive."""
    done: Set[str] = set()
    for key in archive.keys(source="fotmob"):
        body = key[len("fotmob/"):]                # {league}/{type}/{label}__{stamp}.json.gz
        endpoint, _, name = body.rpartition("/")   # endpoint = {league}/{type}
        label = name.rsplit("__", 1)[0]            # {season}__{stat}
        done.add("{}/{}".format(endpoint, label))
    return done


@register_source
class FotmobSource:
    name = "fotmob"

    def ingest(
        self,
        archive: RawArchive,
        leagues: Optional[Sequence[str]] = None,
        seasons: Optional[Sequence[str]] = None,
        refetch: bool = False,
        captured_at: Optional[dt.datetime] = None,
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
        done = set() if refetch else archived_stats(archive)
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
                            "source": self.name, "endpoint": "{}/{}".format(slug, stat_type),
                            "league": slug, "league_id": league_id, "season": label,
                            "season_id": season_id, "stat": stat, "stat_type": stat_type,
                            "url": BASE, "fetched_at": captured_at.isoformat() + "Z",
                            "data": data,
                        }
                        written.append(archive.write(
                            self.name, "{}/{}".format(slug, stat_type), envelope,
                            label="{}__{}".format(dash, stat), captured_at=captured_at))
                        done.add(marker)
                        time.sleep(_POLITENESS_SECONDS)
                log.info("archived fotmob %s %s", slug, label)

        return written
