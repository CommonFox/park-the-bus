"""DraftKings snapshot -> src_draftkings_odds (raw decimal odds).

One envelope holds a moneyline and a totals payload. Rows are joined by event
id: a row is written only when an event has the full 1X2 and the Over/Under 2.5
line. Odds are stored raw; de-vigging into probabilities is the fdr solver's job.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, Optional

import duckdb

from ..load import LOADERS

_TOTALS_LINE = 2.5
_COLUMNS = [
    "dk_event_id", "captured_at", "season", "kickoff_utc", "home_team", "away_team",
    "moneyline_home", "moneyline_draw", "moneyline_away", "over_2_5", "under_2_5",
    "archive_key",
]


def _f(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ts(value: Any) -> Optional[dt.datetime]:
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
        price = _f((sel.get("displayOdds") or {}).get("decimal"))
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


def load_draftkings(con: duckdb.DuckDBPyConnection, payload: dict, archive_key: str) -> int:
    captured_at = _ts(payload.get("captured_at"))
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
        kickoff = _ts(event.get("startEventDate"))
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


LOADERS["draftkings"] = load_draftkings
