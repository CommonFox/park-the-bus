"""Resolve a source's player to one `dim_player` row.

FPL's `code` is stable across seasons where `element_id` is reassigned, so it
forms the spine: one dim_player row per code. Understat and FotMob players are
then matched into that spine by three deterministic tiers and a scored fallback.

Where two candidates score too closely to separate, resolution fails and records
the reason instead of guessing -- the same contract as match resolution.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import List, NamedTuple, Optional, Sequence

import duckdb

from .names import score_candidate, similarity
from .text import normalize_name

log = logging.getLogger(__name__)

AMBIGUOUS = "ambiguous"

SCORE_THRESHOLD = 0.80
SCORE_MARGIN = 0.10


class Candidate(NamedTuple):
    player_id: int
    canonical_name: str
    birth_date: Optional[dt.date]
    opta_code: Optional[str]
    team_name: Optional[str]


class Match(NamedTuple):
    """player_id set means matched. reason set means refuse and record.

    Both unset means no candidate was good enough, which is not a failure --
    it is how a player who has never appeared in FPL enters the dimension.
    """
    player_id: Optional[int] = None
    method: Optional[str] = None
    confidence: Optional[float] = None
    reason: Optional[str] = None
    detail: Optional[str] = None


def match_player(
    candidates: Sequence[Candidate],
    *,
    name: str,
    team_name: Optional[str],
    birth_date: Optional[dt.date],
    opta_code: Optional[str],
) -> Match:
    """Deterministic tiers first, then the scored fallback."""
    normalized = normalize_name(name)

    if opta_code:
        for candidate in candidates:
            if candidate.opta_code and candidate.opta_code == opta_code:
                return Match(candidate.player_id, "opta_code", 1.00)

    if birth_date is not None:
        for candidate in candidates:
            if (candidate.birth_date == birth_date
                    and normalize_name(candidate.canonical_name) == normalized):
                return Match(candidate.player_id, "name_dob", 0.99)

    if team_name is not None:
        normalized_team = normalize_name(team_name)
        exact = [
            candidate.player_id for candidate in candidates
            if normalize_name(candidate.canonical_name) == normalized
            and candidate.team_name is not None
            and normalize_name(candidate.team_name) == normalized_team
        ]
        # More than one means genuine homonyms at one club: the tier cannot
        # separate them, so it declines and leaves it to the fallback.
        if len(exact) == 1:
            return Match(exact[0], "name_team_season", 0.95)

    return _scored_match(
        candidates, name=name, team_name=team_name, birth_date=birth_date)


def _scored_match(
    candidates: Sequence[Candidate],
    *,
    name: str,
    team_name: Optional[str],
    birth_date: Optional[dt.date],
) -> Match:
    """Best candidate above the threshold, provided it clears the runner-up.

    The margin is what stops a confident-looking wrong match when two players
    in one block score alike -- exactly the homonym case the tiers declined.
    """
    normalized_team = normalize_name(team_name) if team_name is not None else None

    scored: List[tuple] = []
    for candidate in candidates:
        team_agrees = None
        if normalized_team is not None and candidate.team_name is not None:
            team_agrees = normalize_name(candidate.team_name) == normalized_team
        birth_date_agrees = None
        if birth_date is not None and candidate.birth_date is not None:
            birth_date_agrees = candidate.birth_date == birth_date

        scored.append((
            score_candidate(
                name_similarity=similarity(name, candidate.canonical_name),
                team_agrees=team_agrees,
                birth_date_agrees=birth_date_agrees,
            ),
            candidate.player_id,
        ))

    if not scored:
        return Match()

    scored.sort(reverse=True)
    best_score, best_id = scored[0]
    if best_score < SCORE_THRESHOLD:
        return Match()

    runner_up = scored[1][0] if len(scored) > 1 else 0.0
    if best_score - runner_up < SCORE_MARGIN:
        return Match(
            reason=AMBIGUOUS,
            detail="{:.3f} vs {:.3f}".format(best_score, runner_up),
        )
    return Match(best_id, "scored", best_score)


def build_fpl_spine(con: duckdb.DuckDBPyConnection) -> int:
    """Create one dim_player row per distinct FPL code. Returns rows created.

    Idempotent: a code already mapped in map_player_source is skipped, the
    same check-before-create shape as resolve_match. Rerunning against
    unchanged src_fpl_element data therefore creates nothing and returns 0.

    birth_date and opta_code are taken as the latest non-null across seasons
    rather than the latest season's value: FPL only began publishing them
    recently, so the most recent season is not reliably the populated one.
    """
    rows = con.execute(
        "SELECT code, "
        "       arg_max(trim(coalesce(first_name, '') || ' ' "
        "                    || coalesce(second_name, '')), season) AS canonical_name, "
        "       max(birth_date) AS birth_date, "
        "       max(opta_code)  AS opta_code, "
        "       min(season)     AS first_season, "
        "       max(season)     AS last_season "
        "FROM src_fpl_element WHERE code IS NOT NULL "
        "GROUP BY code ORDER BY code"
    ).fetchall()

    created = 0
    for code, canonical, birth_date, opta_code, first_season, last_season in rows:
        existing = con.execute(
            "SELECT player_id FROM map_player_source "
            "WHERE source = 'fpl' AND source_player_id = ?",
            [str(code)],
        ).fetchone()
        if existing is not None:
            continue

        player_id = con.execute("SELECT nextval('seq_player_id')").fetchone()[0]
        con.execute(
            "INSERT INTO dim_player (player_id, canonical_name, normalized_name, "
            "birth_date, fpl_code, opta_code, first_seen_season, last_seen_season) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [player_id, canonical, normalize_name(canonical), birth_date, code,
             opta_code, first_season, last_season],
        )
        con.execute(
            "INSERT INTO map_player_source (player_id, source, source_player_id, "
            "method, confidence) VALUES (?, ?, ?, ?, ?)",
            [player_id, "fpl", str(code), "fpl_code", 1.0],
        )
        created += 1
    return created
