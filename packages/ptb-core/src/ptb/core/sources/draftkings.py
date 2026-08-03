"""DraftKings -- forward-looking Premier League fixture odds.

Prices fixtures that have not been played yet, so the FPL fdr solver can rate
the gameweek ahead. Two subcategories make a full price: moneyline (1X2) and
total goals Over/Under 2.5. Both raw payloads are archived verbatim in one
snapshot per run; odds are live, so snapshots are append-only.

The endpoint is gated on the web client's Akamai fingerprint headers, and
x-client-version is pinned to a web build that will eventually stop being
accepted. That failure is routine, not fatal: a Blocked gate returns zero
payloads and the run continues. Keeping this behind the standard Source
interface also means a future swap to another odds book touches nothing else.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import requests

from .. import config
from ..archive import RawArchive
from .registry import register_source

log = logging.getLogger(__name__)

BASE = ("https://sportsbook-nash.draftkings.com/sites/{site}/api/sportscontent"
        "/controldata/league/leagueSubcategory/v1/markets")
SITE = "US-CO-SB"
PREMIER_LEAGUE = "40253"
SUBCATEGORY_MONEYLINE = "4514"
SUBCATEGORY_TOTALS = "13171"

# Akamai gates on these; origin + referer + a browser UA alone returns 403.
HEADERS = {
    "accept": "*/*",
    "accept-language": "en-US,en;q=0.8",
    "origin": "https://sportsbook.draftkings.com",
    "referer": "https://sportsbook.draftkings.com/",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
    "user-agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"),
    "x-client-feature": "leagueSubcategory",
    "x-client-name": "web",
    "x-client-page": "league",
    "x-client-version": "2630.3.1.9",
    "x-client-widget-name": "cms",
    "x-client-widget-version": "1.0.0",
    "x-pe-cn": "web",
    "x-pe-cv": "2630.3.1.9",
    "x-pe-ep": "SB",
    "x-pe-loc": "US-CO",
}


class Blocked(RuntimeError):
    """DraftKings refused the request -- fingerprint gate changed, or geo.

    Expected enough to catch: the run falls back rather than aborting.
    """


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    return session


def _fetch_subcategory(session, subcategory, league=PREMIER_LEAGUE, site=SITE) -> Dict[str, Any]:
    query = {
        "isBatchable": "false",
        "templateVars": "{},{}".format(league, subcategory),
        "eventsQuery": ("$filter=leagueId eq '{}' AND "
                        "clientMetadata/Subcategories/any(s: s/Id eq '{}')").format(league, subcategory),
        "marketsQuery": ("$filter=clientMetadata/subCategoryId eq '{}' "
                         "AND tags/all(t: t ne 'SportcastBetBuilder')").format(subcategory),
        "include": "Events",
        "entity": "events",
    }
    url = BASE.format(site=site) + "?" + urlencode(query)
    try:
        resp = session.get(url, timeout=config.REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        raise Blocked("draftkings request failed: {}".format(exc))
    if resp.status_code >= 400:
        raise Blocked("draftkings returned {} for subcategory {}".format(resp.status_code, subcategory))
    try:
        return resp.json()
    except ValueError as exc:
        raise Blocked("draftkings returned non-JSON: {}".format(exc))


@register_source
class DraftkingsSource:
    name = "draftkings"

    def ingest(
        self,
        archive: RawArchive,
        captured_at: Optional[dt.datetime] = None,
        **_ignored,
    ) -> List[str]:
        captured_at = captured_at or dt.datetime.utcnow().replace(microsecond=0)
        session = _session()
        try:
            moneyline = _fetch_subcategory(session, SUBCATEGORY_MONEYLINE, PREMIER_LEAGUE, SITE)
            totals = _fetch_subcategory(session, SUBCATEGORY_TOTALS, PREMIER_LEAGUE, SITE)
        except Blocked as exc:
            log.warning("draftkings blocked, skipping: %s", exc)
            return []

        envelope = {
            "source": self.name, "endpoint": "markets", "league": "premier-league",
            "captured_at": captured_at.isoformat() + "Z",
            "url": BASE.format(site=SITE),
            "fetched_at": captured_at.isoformat() + "Z",
            "data": {"moneyline": moneyline, "totals": totals},
        }
        key = archive.write(self.name, "markets", envelope, captured_at=captured_at)
        log.info("archived draftkings snapshot (%d events)",
                 len(moneyline.get("events") or []))
        return [key]
