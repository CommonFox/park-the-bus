"""vaastav/Fantasy-Premier-League -- historical FPL backfill.

The official API serves only the current season. This community repo publishes
cleaned per-gameweek CSVs back to 2016/17. One payload per season bundles
players_raw.csv and gws/merged_gw.csv, wrapped verbatim. Static history, so a
season already archived is skipped unless `refetch`.
"""
from __future__ import annotations

import datetime as dt
import logging
import random
import time
from typing import List, Optional, Sequence, Set

import requests

from .. import config
from ..archive import RawArchive
from .registry import register_source

log = logging.getLogger(__name__)

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


def archived_seasons(archive: RawArchive) -> Set[str]:
    prefix = "vaastav/season/"
    seasons: Set[str] = set()
    for key in archive.keys(source="vaastav", endpoint="season"):
        seasons.add(key[len(prefix):].split("__", 1)[0])
    return seasons


@register_source
class VaastavSource:
    name = "vaastav"

    def ingest(
        self,
        archive: RawArchive,
        seasons: Optional[Sequence[str]] = None,
        refetch: bool = False,
        captured_at: Optional[dt.datetime] = None,
        **_ignored,
    ) -> List[str]:
        seasons = [to_dash(s) for s in (seasons or KNOWN_SEASONS)]
        captured_at = captured_at or dt.datetime.utcnow().replace(microsecond=0)
        session = _session()
        done = set() if refetch else archived_seasons(archive)
        written: List[str] = []

        for dash in seasons:
            if dash in done:
                continue
            players = _fetch_csv(session, "{}/{}/players_raw.csv".format(RAW_BASE, dash))
            merged = _fetch_csv(session, "{}/{}/gws/merged_gw.csv".format(RAW_BASE, dash))
            envelope = {
                "source": self.name, "endpoint": "season", "season": season_label(dash),
                "url": "{}/{}/".format(RAW_BASE, dash),
                "fetched_at": captured_at.isoformat() + "Z",
                "data": {"players_raw": players, "merged_gw": merged},
            }
            written.append(archive.write(
                self.name, "season", envelope, label=dash, captured_at=captured_at))
            log.info("archived vaastav %s", dash)
            time.sleep(_POLITENESS_SECONDS)

        return written
