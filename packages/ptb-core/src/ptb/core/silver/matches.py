"""Resolve a source's match to one `dim_match` row.

Matching on exact date breaks on late kickoffs: a 19:30 Pacific Saturday NWSL
game is 02:30 UTC on Sunday, so a source reporting local date and one
reporting UTC would produce two matches for one fixture -- and every
cross-source join would silently return nothing. Resolution therefore uses a
+/-36 hour window around kickoff rather than date equality.

Where more than one candidate falls inside the window, resolution fails and
records the reason instead of guessing.

This module is the shared machinery only. Which rows a source offers up for
resolution is that source's own business, so each source module owns a
`resolve_matches(con)` that queries its `src_` tables and calls
`resolve_match` per row; `warehouse.rebuild` invokes them.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Optional

import duckdb

from .names import normalize_name
from .teams import resolve_team

log = logging.getLogger(__name__)

RESOLUTION_WINDOW = dt.timedelta(hours=36)

MULTIPLE_CANDIDATES = "multiple_candidates"
UNPARSEABLE = "unparseable"


def match_key(
    competition: str,
    season: str,
    home_normalized: str,
    away_normalized: str,
    kickoff: dt.datetime,
) -> str:
    """Human-readable identity string. Debugging aid only -- never join on it."""
    return "{}|{}|{}|{}-{}".format(
        competition, season, kickoff.date().isoformat(),
        home_normalized, away_normalized,
    )


def _record_unresolved(
    con: duckdb.DuckDBPyConnection,
    source: str,
    source_match_id: str,
    reason: str,
    detail: Optional[str] = None,
) -> None:
    con.execute(
        "INSERT OR REPLACE INTO unresolved_match "
        "(source, source_match_id, reason, detail) VALUES (?, ?, ?, ?)",
        [source, source_match_id, reason, detail],
    )


def resolve_match(
    con: duckdb.DuckDBPyConnection,
    *,
    source: str,
    source_match_id: str,
    competition: str,
    season: str,
    kickoff: dt.datetime,
    home_team: str,
    away_team: str,
) -> Optional[int]:
    """Find or create the match. Returns its id, or None if ambiguous."""
    existing = con.execute(
        "SELECT match_id FROM map_match_source WHERE source = ? AND source_match_id = ?",
        [source, source_match_id],
    ).fetchone()
    if existing is not None:
        return existing[0]

    home_id = resolve_team(con, home_team, source=source)
    away_id = resolve_team(con, away_team, source=source)

    candidates = con.execute(
        "SELECT match_id FROM dim_match "
        "WHERE competition = ? AND season = ? "
        "  AND home_team_id = ? AND away_team_id = ? "
        "  AND abs(epoch(kickoff_utc) - epoch(?::TIMESTAMP)) <= ?",
        [competition, season, home_id, away_id, kickoff,
         RESOLUTION_WINDOW.total_seconds()],
    ).fetchall()

    if len(candidates) > 1:
        log.warning(
            "ambiguous match: %s/%s has %d candidates within %s",
            source, source_match_id, len(candidates), RESOLUTION_WINDOW,
        )
        _record_unresolved(
            con, source, source_match_id, MULTIPLE_CANDIDATES,
            "{} candidates within {}".format(len(candidates), RESOLUTION_WINDOW),
        )
        return None

    if candidates:
        match_id = candidates[0][0]
        method = "window"
        confidence = 0.9
    else:
        match_id = con.execute("SELECT nextval('seq_match_id')").fetchone()[0]
        con.execute(
            "INSERT INTO dim_match (match_id, competition, season, kickoff_utc, "
            "home_team_id, away_team_id, match_key) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [match_id, competition, season, kickoff, home_id, away_id,
             match_key(competition, season, normalize_name(home_team),
                       normalize_name(away_team), kickoff)],
        )
        method = "created"
        confidence = 1.0

    con.execute(
        "INSERT OR REPLACE INTO map_match_source "
        "(match_id, source, source_match_id, method, confidence) VALUES (?, ?, ?, ?, ?)",
        [match_id, source, source_match_id, method, confidence],
    )
    con.execute(
        "DELETE FROM unresolved_match WHERE source = ? AND source_match_id = ?",
        [source, source_match_id],
    )
    return match_id
