"""Understat -- shot-level xG for the Big 5 men's leagues.

Understat was reworked to a JS shell in late 2025; data comes from plain JSON
endpoints, not embedded HTML. `getLeagueData/{league}/{year}` returns
{teams, players, dates}; `getMatchData/{id}` returns {shots: {h, a}, ...}.

Ingest is two-phase: one league payload per league-season (giving the match
list and match-level xG), then one shot payload per played match. The match
phase is incremental -- a match already in the archive is skipped unless
`refetch` is set -- because a full shot sweep is ~2000 requests per season.
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


def archived_match_ids(archive: RawArchive) -> Set[str]:
    """Understat match ids already present in the archive."""
    prefix = "understat/match/"
    ids: Set[str] = set()
    for key in archive.keys(source="understat", endpoint="match"):
        name = key[len(prefix):]
        ids.add(name.split("__", 1)[0])
    return ids


@register_source
class UnderstatSource:
    name = "understat"

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
        seasons = list(seasons or [dt.date.today().year - (0 if dt.date.today().month >= 7 else 1)])
        captured_at = captured_at or dt.datetime.utcnow().replace(microsecond=0)

        unknown = [lg for lg in leagues if lg not in LEAGUE_TO_COMPETITION]
        if unknown:
            raise ValueError("unknown league(s): {}".format(", ".join(unknown)))

        session = _session()
        seen = set() if refetch else archived_match_ids(archive)
        written: List[str] = []

        for start_year in seasons:
            season = season_label(start_year)
            for league in leagues:
                data = _fetch_league(session, league, start_year)
                url = "{}/getLeagueData/{}/{}".format(BASE, league, start_year)
                envelope = {
                    "source": self.name, "endpoint": "league",
                    "league": league, "competition": LEAGUE_TO_COMPETITION[league],
                    "season": season, "url": url,
                    "fetched_at": captured_at.isoformat() + "Z", "data": data,
                }
                written.append(archive.write(
                    self.name, "league", envelope,
                    label="{}__{}".format(league, season.replace("/", "-")),
                    captured_at=captured_at,
                ))
                log.info("archived understat league %s %s", league, season)
                time.sleep(_POLITENESS_SECONDS)

                match_ids = [str(d["id"]) for d in data.get("dates", []) if d.get("isResult")]
                for match_id in match_ids:
                    if match_id in seen:
                        continue
                    shot_data = _fetch_match(session, match_id)
                    match_env = {
                        "source": self.name, "endpoint": "match",
                        "understat_match_id": match_id,
                        "url": "{}/getMatchData/{}".format(BASE, match_id),
                        "fetched_at": captured_at.isoformat() + "Z", "data": shot_data,
                    }
                    written.append(archive.write(
                        self.name, "match", match_env,
                        label=match_id, captured_at=captured_at,
                    ))
                    seen.add(match_id)
                    time.sleep(_POLITENESS_SECONDS)
                log.info("archived understat shots %s %s (%d matches)", league, season, len(match_ids))

        return written
