"""football-data.co.uk -- results, match statistics and bookmaker odds.

Free, no key: one CSV per competition-season at /mmz4281/{code}/{comp}.csv,
back to 1993/94. Static files, so this is the one source here with no
scraping fragility -- which is why it goes first.

Column availability varies enormously by era: 1993/94 has 28 columns and no
match statistics at all, 2000/01 has 45 including shots but no kickoff time,
2024/25 has 120. Even within a season the count differs between
competitions. Nothing here may assume a fixed schema; the loader reads by
column name and tolerates absence.
"""
from __future__ import annotations

import datetime as dt
import logging
import time
from typing import Dict, List, Optional, Sequence

import requests

from .. import config
from ..archive import RawArchive
from .registry import register_source

log = logging.getLogger(__name__)

BASE = "https://www.football-data.co.uk/mmz4281"

COMPETITIONS: Dict[str, Dict[str, object]] = {
    "E0": {"country": "England", "name": "Premier League", "tier": 1},
    "E1": {"country": "England", "name": "Championship", "tier": 2},
    "SP1": {"country": "Spain", "name": "La Liga", "tier": 1},
    "SP2": {"country": "Spain", "name": "Segunda Division", "tier": 2},
    "I1": {"country": "Italy", "name": "Serie A", "tier": 1},
    "I2": {"country": "Italy", "name": "Serie B", "tier": 2},
    "D1": {"country": "Germany", "name": "Bundesliga", "tier": 1},
    "D2": {"country": "Germany", "name": "2. Bundesliga", "tier": 2},
    "F1": {"country": "France", "name": "Ligue 1", "tier": 1},
    "F2": {"country": "France", "name": "Ligue 2", "tier": 2},
}

BIG_5 = ("E0", "SP1", "I1", "D1", "F1")

_POLITENESS_SECONDS = 1.0


class NotPublishedError(RuntimeError):
    """football-data has no file for this competition-season yet."""


def season_code(start_year: int) -> str:
    """2024 -> '2425', 1999 -> '9900'."""
    return "{:02d}{:02d}".format(start_year % 100, (start_year + 1) % 100)


def season_label(start_year: int) -> str:
    """2024 -> '2024/25'. The warehouse convention, used everywhere."""
    return "{}/{:02d}".format(start_year, (start_year + 1) % 100)


def parse_season(label: str) -> int:
    """'2024/25' -> 2024. Also tolerates the dash form '2024-25' (used by
    vaastav) so a single --season flag works across every source."""
    return int(label.replace("-", "/").split("/")[0])


def decode_csv(raw: bytes) -> str:
    """football-data files carry a UTF-8 BOM, and the older ones are latin-1
    with occasional stray bytes. utf-8-sig handles the BOM; latin-1 never
    fails, so it is a safe last resort."""
    for encoding in ("utf-8-sig", "latin-1"):
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        return text.lstrip("﻿")
    raise ValueError("could not decode payload")


def _fetch_csv(url: str) -> str:
    session = requests.Session()
    session.headers.update({"User-Agent": config.USER_AGENT})
    response = session.get(url, timeout=config.REQUEST_TIMEOUT)
    if response.status_code == 404:
        raise NotPublishedError(url)
    response.raise_for_status()
    if not response.content.strip():
        raise NotPublishedError(url)
    return decode_csv(response.content)


@register_source
class FootballDataSource:
    name = "footballdata"

    def ingest(
        self,
        archive: RawArchive,
        competitions: Optional[Sequence[str]] = None,
        seasons: Optional[Sequence[int]] = None,
        captured_at: Optional[dt.datetime] = None,
        **_ignored,
    ) -> List[str]:
        competitions = list(competitions or BIG_5)
        seasons = list(seasons or [dt.date.today().year - (0 if dt.date.today().month >= 7 else 1)])
        captured_at = captured_at or dt.datetime.utcnow().replace(microsecond=0)

        unknown = [c for c in competitions if c not in COMPETITIONS]
        if unknown:
            raise ValueError("unknown competition(s): {}".format(", ".join(unknown)))

        written: List[str] = []
        for season in seasons:
            for competition in competitions:
                url = "{}/{}/{}.csv".format(BASE, season_code(season), competition)
                try:
                    csv_text = _fetch_csv(url)
                except NotPublishedError:
                    log.info("not published yet: %s %s", competition, season_label(season))
                    continue

                envelope = {
                    "source": self.name,
                    "competition": competition,
                    "season": season_label(season),
                    "url": url,
                    "fetched_at": captured_at.isoformat() + "Z",
                    "csv": csv_text,
                }
                key = archive.write(
                    self.name,
                    "season",
                    envelope,
                    label="{}__{}".format(competition, season_label(season).replace("/", "-")),
                    captured_at=captured_at,
                )
                written.append(key)
                log.info("archived %s %s", competition, season_label(season))
                time.sleep(_POLITENESS_SECONDS)

        return written
