"""DraftKings -- forward-looking Premier League fixture odds.

Prices fixtures that have not been played yet, so the FPL fdr solver can rate
the gameweek ahead. Two subcategories make a full price: moneyline (1X2) and
total goals Over/Under 2.5. Both raw payloads are archived verbatim in one
snapshot per run; odds are live, so snapshots are append-only.

The endpoint is gated on the web client's Akamai fingerprint headers, and
x-client-version is pinned to a web build that will eventually stop being
accepted. That failure is routine, not fatal: a Blocked gate returns zero
payloads and the run continues.

Rows are joined by event id on load: a row is written only when an event has
the full 1X2 and the Over/Under 2.5 line. Odds are stored raw; de-vigging into
probabilities is the gold layer's job.
"""
from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import duckdb
import requests

from .. import archive, config
from .coerce import as_float
from .matches import resolve_match

log = logging.getLogger(__name__)

NAME = "draftkings"

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


# --------------------------------------------------------------- ingest
# Network -> archive. Never touches the warehouse.

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


def ingest(
    captured_at: Optional[dt.datetime] = None,
    root: Optional[Path] = None,
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
        "source": NAME, "endpoint": "markets", "league": "premier-league",
        "captured_at": captured_at.isoformat() + "Z",
        "url": BASE.format(site=SITE),
        "fetched_at": captured_at.isoformat() + "Z",
        "data": {"moneyline": moneyline, "totals": totals},
    }
    key = archive.write(NAME, "markets", envelope, captured_at=captured_at, root=root)
    log.info("archived draftkings snapshot (%d events)",
             len(moneyline.get("events") or []))
    return [key]


# ----------------------------------------------------------------- load
# Archive -> src_draftkings_odds. Never touches the network.

_TOTALS_LINE = 2.5
_COLUMNS = [
    "dk_event_id", "captured_at", "season", "kickoff_utc", "home_team", "away_team",
    "moneyline_home", "moneyline_draw", "moneyline_away", "over_2_5", "under_2_5",
    "archive_key",
]


def _timestamp(value: Any) -> Optional[dt.datetime]:
    if not value:
        return None
    text = str(value).replace("Z", "").strip()
    # DraftKings uses 7-digit fractional seconds; trim to microseconds if present.
    if "." in text:
        head, frac = text.split(".", 1)
        text = "{}.{}".format(head, frac[:6])
    try:
        return dt.datetime.fromisoformat(text)
    except ValueError:
        try:
            return dt.datetime.fromisoformat(text.split(".")[0])
        except ValueError:
            return None


def season_from_kickoff(kickoff: dt.datetime) -> str:
    """A Premier League season runs Aug-May; an Aug 2026 kickoff is '2026/27'."""
    start = kickoff.year if kickoff.month >= 7 else kickoff.year - 1
    return "{}/{:02d}".format(start, (start + 1) % 100)


def _odds_by_event(payload: dict, totals: bool) -> Dict[str, Dict[str, float]]:
    market_event = {m["id"]: m.get("eventId") for m in payload.get("markets") or []}
    out: Dict[str, Dict[str, float]] = {}
    for sel in payload.get("selections") or []:
        if totals and sel.get("points") != _TOTALS_LINE:
            continue
        event_id = market_event.get(sel.get("marketId"))
        outcome = sel.get("outcomeType")
        price = as_float((sel.get("displayOdds") or {}).get("decimal"))
        if event_id and outcome and price and price > 0:
            out.setdefault(event_id, {})[outcome] = price
    return out


def _teams(event: dict) -> tuple:
    participants = event.get("participants") or []
    home = next((p.get("name") for p in participants if p.get("venueRole") == "Home"), None)
    away = next((p.get("name") for p in participants if p.get("venueRole") == "Away"), None)
    if not home or not away:
        name = event.get("name") or ""
        if " vs " in name:
            home, away = [part.strip() for part in name.split(" vs ", 1)]
    return home, away


def load(con: duckdb.DuckDBPyConnection, payload: dict, archive_key: str) -> int:
    captured_at = _timestamp(payload.get("captured_at"))
    data = payload["data"]
    moneyline, totals = data.get("moneyline") or {}, data.get("totals") or {}

    events = {e["id"]: e for e in moneyline.get("events") or []}
    ml_odds = _odds_by_event(moneyline, totals=False)
    ou_odds = _odds_by_event(totals, totals=True)

    rows = []
    for event_id, event in events.items():
        one = ml_odds.get(event_id) or {}
        over_under = ou_odds.get(event_id) or {}
        if not all(k in one for k in ("Home", "Tie", "Away")):
            continue
        if not all(k in over_under for k in ("Over", "Under")):
            continue
        home, away = _teams(event)
        if not home or not away:
            continue
        kickoff = _timestamp(event.get("startEventDate"))
        season = season_from_kickoff(kickoff) if kickoff else None
        rows.append([
            event_id, captured_at, season, kickoff, home, away,
            one["Home"], one["Tie"], one["Away"],
            over_under["Over"], over_under["Under"], archive_key,
        ])

    if rows:
        con.executemany(
            "INSERT OR REPLACE INTO src_draftkings_odds ({}) VALUES ({})".format(
                ", ".join(_COLUMNS), ", ".join("?" * len(_COLUMNS))),
            rows)
    return len(rows)


# ------------------------------------------------------------- identity

def resolve_matches(con: duckdb.DuckDBPyConnection) -> int:
    """Resolve DraftKings events into dim_match. Idempotent.

    Odds are snapshotted, so only the latest snapshot per event is resolved.
    DraftKings events are upcoming Premier League matches, so they resolve
    against the FPL forward fixtures (and, once played, football-data) already in
    dim_match via the +/-36h window.
    """
    rows = con.execute(
        "SELECT dk_event_id, season, kickoff_utc, home_team, away_team FROM ("
        "  SELECT *, row_number() OVER "
        "    (PARTITION BY dk_event_id ORDER BY captured_at DESC) AS rn "
        "  FROM src_draftkings_odds WHERE kickoff_utc IS NOT NULL AND season IS NOT NULL"
        ") WHERE rn = 1 ORDER BY kickoff_utc, dk_event_id"
    ).fetchall()

    resolved = 0
    for event_id, season, kickoff, home, away in rows:
        if resolve_match(
            con, source=NAME, source_match_id=str(event_id),
            competition="E0", season=season, kickoff=kickoff,
            home_team=home, away_team=away,
        ) is not None:
            resolved += 1
    return resolved
