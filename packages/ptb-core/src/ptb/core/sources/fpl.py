"""FPL API -- the official Fantasy Premier League public endpoints.

Three payloads per run: one bootstrap-static (players, teams, events,
positions), one fixtures list, and one element-summary per player. The API
serves only the current season and its data mutates every gameweek, so every
run fetches a fresh, timestamped snapshot -- the archive is append-only, and a
rebuild simply loads the latest snapshot per key. There is no incremental skip;
skipping already-archived players would miss each new gameweek's history.
"""
from __future__ import annotations

import datetime as dt
import logging
import random
import time
from typing import Any, Dict, List, Optional

import requests

from .. import config
from ..archive import RawArchive
from .registry import register_source

log = logging.getLogger(__name__)

BASE = "https://fantasy.premierleague.com/api"
_POLITENESS_SECONDS = 0.3


def season_from_events(events: List[Dict[str, Any]]) -> str:
    """Derive '2024/25' from the earliest event deadline year."""
    years = [int(e["deadline_time"][:4]) for e in events if e.get("deadline_time")]
    start = min(years) if years else dt.date.today().year
    return "{}/{:02d}".format(start, (start + 1) % 100)


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


@register_source
class FplSource:
    name = "fpl"

    def ingest(
        self,
        archive: RawArchive,
        captured_at: Optional[dt.datetime] = None,
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
            self.name, "bootstrap",
            {"source": self.name, "endpoint": "bootstrap", "season": season,
             "url": "{}/bootstrap-static/".format(BASE), "fetched_at": stamp,
             "data": bootstrap},
            label=label, captured_at=captured_at))

        fixtures = _fetch_fixtures(session)
        written.append(archive.write(
            self.name, "fixtures",
            {"source": self.name, "endpoint": "fixtures", "season": season,
             "url": "{}/fixtures/".format(BASE), "fetched_at": stamp,
             "data": fixtures},
            label=label, captured_at=captured_at))
        time.sleep(_POLITENESS_SECONDS)

        for element in bootstrap.get("elements", []):
            eid = element["id"]
            data = _fetch_element(session, eid)
            written.append(archive.write(
                self.name, "element",
                {"source": self.name, "endpoint": "element", "season": season,
                 "element_id": eid, "url": "{}/element-summary/{}/".format(BASE, eid),
                 "fetched_at": stamp, "data": data},
                label=str(eid), captured_at=captured_at))
            time.sleep(_POLITENESS_SECONDS)

        log.info("archived fpl %s: bootstrap + fixtures + %d elements",
                 season, len(bootstrap.get("elements", [])))
        return written
