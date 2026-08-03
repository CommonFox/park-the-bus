"""Resolve a source's match to one `dim_match` row.

Matching on exact date breaks on late kickoffs: a 19:30 Pacific Saturday NWSL
game is 02:30 UTC on Sunday, so a source reporting local date and one
reporting UTC would produce two matches for one fixture -- and every
cross-source join would silently return nothing. Resolution therefore uses a
+/-36 hour window around kickoff rather than date equality.

Where more than one candidate falls inside the window, resolution fails and
records the reason instead of guessing.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Optional

import duckdb

from .teams import resolve_team
from .text import normalize_name

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


def resolve_footballdata(con: duckdb.DuckDBPyConnection) -> int:
    """Resolve every football-data row into dim_match. Idempotent."""
    rows = con.execute(
        "SELECT competition, season, match_date, kickoff_time, home_team, away_team "
        "FROM src_footballdata_match ORDER BY match_date, home_team"
    ).fetchall()

    resolved = 0
    for competition, season, match_date, kickoff_time, home, away in rows:
        kickoff = dt.datetime.combine(match_date, kickoff_time or dt.time(15, 0))
        source_match_id = "{}|{}|{}|{}|{}".format(
            competition, season, match_date.isoformat(), home, away
        )
        if resolve_match(
            con, source="footballdata", source_match_id=source_match_id,
            competition=competition, season=season, kickoff=kickoff,
            home_team=home, away_team=away,
        ) is not None:
            resolved += 1
    return resolved
