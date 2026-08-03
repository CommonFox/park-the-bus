"""ASA (American Soccer Analysis) -- games, xG and goals-added for the US leagues.

Free documented REST API at app.americansocceranalysis.com/api/v1/{league}/...
Reference resources (teams, players) carry no season and are re-fetched each run
(they are small). Seasonal resources (games and the per-season stat tables) are
incremental: a (league, resource, season) already archived is skipped unless
`refetch` is set.

Games and players are keyed by hashed ids, not names, so resolution later joins
games to the teams table for names.
"""
from __future__ import annotations

import datetime as dt
import logging
import random
import time
from typing import Any, List, Optional, Sequence

import requests

from .. import config
from ..archive import RawArchive
from .registry import register_source

log = logging.getLogger(__name__)

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


def _seasonal_archived(archive: RawArchive, league: str, resource: str, season: str) -> bool:
    endpoint = "{}/{}".format(league, _resource_label(resource))
    prefix = "asa/{}/{}__".format(endpoint, season)
    return any(k.startswith(prefix) for k in archive.keys(source="asa", endpoint=endpoint))


@register_source
class AsaSource:
    name = "asa"

    def ingest(
        self,
        archive: RawArchive,
        leagues: Optional[Sequence[str]] = None,
        seasons: Optional[Sequence[int]] = None,
        refetch: bool = False,
        captured_at: Optional[dt.datetime] = None,
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
                written.append(self._archive(
                    archive, league, resource, None, data, captured_at))
                time.sleep(_POLITENESS_SECONDS)

            for start_year in seasons:
                season = str(start_year)
                for resource in SEASONAL_RESOURCES:
                    if not refetch and _seasonal_archived(archive, league, resource, season):
                        continue
                    data = _fetch(session, league, resource, season)
                    written.append(self._archive(
                        archive, league, resource, season, data, captured_at))
                    time.sleep(_POLITENESS_SECONDS)
                log.info("archived asa %s %s", league, season)

        return written

    def _archive(self, archive, league, resource, season, data, captured_at) -> str:
        endpoint = "{}/{}".format(league, _resource_label(resource))
        envelope = {
            "source": self.name, "endpoint": endpoint,
            "league": league, "resource": resource, "season": season,
            "url": "{}/{}/{}".format(BASE, league, resource),
            "fetched_at": captured_at.isoformat() + "Z", "data": data,
        }
        return archive.write(
            self.name, endpoint, envelope,
            label=season, captured_at=captured_at,
        )
