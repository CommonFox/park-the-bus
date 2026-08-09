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
from pathlib import Path
from typing import List, NamedTuple, Optional, Sequence

import duckdb
import yaml

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
    unchanged source data therefore creates nothing and returns 0.

    birth_date and opta_code are taken as the latest non-null across seasons
    rather than the latest season's value: FPL only began publishing them
    recently, so the most recent season is not reliably the populated one.

    src_fpl_element only ever holds the live bootstrap's current season, so a
    player who left the league before that season never appears there. Their
    codes still need spine rows -- src_fpl_element_season (vaastav) is unioned
    in to cover every season it backfills. It carries no birth_date/opta_code,
    so those columns contribute NULL for a code seen only there, which max()
    ignores.
    """
    rows = con.execute(
        "WITH combined AS ("
        "  SELECT code, season, "
        "         trim(coalesce(first_name, '') || ' ' || coalesce(second_name, '')) AS name, "
        "         birth_date, opta_code "
        "  FROM src_fpl_element WHERE code IS NOT NULL "
        "  UNION ALL "
        "  SELECT code, season, "
        "         trim(coalesce(first_name, '') || ' ' || coalesce(second_name, '')) AS name, "
        "         CAST(NULL AS DATE) AS birth_date, CAST(NULL AS VARCHAR) AS opta_code "
        "  FROM src_fpl_element_season WHERE code IS NOT NULL "
        ") "
        "SELECT code, "
        "       arg_max(name, season) AS canonical_name, "
        "       max(birth_date) AS birth_date, "
        "       max(opta_code)  AS opta_code, "
        "       min(season)     AS first_season, "
        "       max(season)     AS last_season "
        "FROM combined "
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


def _fpl_candidates(con: duckdb.DuckDBPyConnection, season: str) -> List[Candidate]:
    """Spine players who appeared in the Premier League in this season.

    Blocking by season keeps each comparison set at roughly 600 players, which
    makes the pairwise cost negligible and removes most homonym risk for free.

    src_fpl_element only ever holds the live bootstrap's current season, so a
    historical season -- anything vaastav backfilled -- has no rows there.
    Falls back to src_fpl_element_season, which vaastav populates for every
    season it covers. That table carries no team name (src_fpl_team is
    likewise current-season-only), so historical candidates match on name and,
    where the spine already carries it, birth_date alone.
    """
    rows = con.execute(
        "SELECT p.player_id, p.canonical_name, p.birth_date, p.opta_code, t.name "
        "FROM dim_player p "
        "JOIN map_player_source m "
        "  ON m.player_id = p.player_id AND m.source = 'fpl' "
        "JOIN src_fpl_element e "
        "  ON CAST(e.code AS VARCHAR) = m.source_player_id AND e.season = ? "
        "JOIN src_fpl_team t ON t.season = e.season AND t.team_id = e.team "
        "ORDER BY p.player_id",
        [season],
    ).fetchall()
    if rows:
        return [Candidate(*row) for row in rows]

    rows = con.execute(
        "SELECT p.player_id, p.canonical_name, p.birth_date, p.opta_code, NULL "
        "FROM dim_player p "
        "JOIN map_player_source m "
        "  ON m.player_id = p.player_id AND m.source = 'fpl' "
        "JOIN src_fpl_element_season e "
        "  ON CAST(e.code AS VARCHAR) = m.source_player_id AND e.season = ? "
        "ORDER BY p.player_id",
        [season],
    ).fetchall()
    return [Candidate(*row) for row in rows]


def _create_player(
    con: duckdb.DuckDBPyConnection,
    name: str,
    season: str,
    birth_date: Optional[dt.date] = None,
) -> int:
    player_id = con.execute("SELECT nextval('seq_player_id')").fetchone()[0]
    con.execute(
        "INSERT INTO dim_player (player_id, canonical_name, normalized_name, "
        "birth_date, first_seen_season, last_seen_season) VALUES (?, ?, ?, ?, ?, ?)",
        [player_id, name, normalize_name(name), birth_date, season, season],
    )
    return player_id


def _resolve_source_players(con, source: str, rows: Sequence[tuple]) -> int:
    """rows: (source_player_id, name, competition, season, team_name).

    Returns the number matched into the spine on this call.

    Blocking is by (competition, season), not season alone. The spine is
    Premier League only, so comparing a La Liga player against it could produce
    a confident wrong match on a similar name -- outside E0 there is simply no
    candidate set, and the player becomes a new dim_player row.

    Rows arrive ordered by season, so a player active across several seasons is
    decided by their earliest appearance and skipped thereafter.

    Idempotent: a source player already present in map_player_source is
    skipped outright, the same check-before-create guard as resolve_match
    (identity/matches.py). It sits ahead of the match/create/ambiguous
    dispatch so it covers all three outcomes, not just the created branch --
    rerunning against unchanged source data therefore creates nothing new and
    returns 0.
    """
    candidate_cache = {}
    seen = set()
    matched = 0

    for source_player_id, name, competition, season, team_name in rows:
        key = str(source_player_id)
        if key in seen or not name:
            continue
        seen.add(key)

        existing = con.execute(
            "SELECT player_id FROM map_player_source "
            "WHERE source = ? AND source_player_id = ?",
            [source, key],
        ).fetchone()
        if existing is not None:
            continue

        block = (competition, season)
        if block not in candidate_cache:
            candidate_cache[block] = (
                _fpl_candidates(con, season) if competition == "E0" else []
            )

        result = match_player(
            candidate_cache[block],
            name=name, team_name=team_name, birth_date=None, opta_code=None,
        )

        if result.reason is not None:
            con.execute(
                "INSERT OR REPLACE INTO unresolved_player "
                "(source, source_player_id, reason, detail) VALUES (?, ?, ?, ?)",
                [source, key, result.reason, result.detail],
            )
            continue

        if result.player_id is None:
            player_id, method, confidence = _create_player(con, name, season), "created", 1.0
        else:
            player_id = result.player_id
            method, confidence = result.method, result.confidence
            matched += 1

        con.execute(
            "INSERT OR REPLACE INTO map_player_source (player_id, source, "
            "source_player_id, method, confidence) VALUES (?, ?, ?, ?, ?)",
            [player_id, source, key, method, confidence],
        )
    return matched


def resolve_understat_players(con: duckdb.DuckDBPyConnection) -> int:
    """Match Understat shot-takers into dim_player. Idempotent.

    A player can move club mid-season, so the modal team across their shots is
    used rather than any single shot's.
    """
    rows = con.execute(
        "SELECT s.player_id, mode(s.player) AS player_name, m.competition, m.season, "
        "       mode(s.team) AS team_name "
        "FROM src_understat_shot s "
        "JOIN src_understat_match m ON m.understat_match_id = s.understat_match_id "
        "WHERE s.player_id IS NOT NULL "
        "GROUP BY s.player_id, m.competition, m.season "
        "ORDER BY m.season, s.player_id"
    ).fetchall()
    return _resolve_source_players(con, "understat", rows)


def resolve_fotmob_players(con: duckdb.DuckDBPyConnection) -> int:
    """Match FotMob leaderboard players into dim_player. Idempotent.

    A player appears on every stat board for their league and season, so rows
    are collapsed to one per (player, season). Team names live on the team
    boards rather than the player rows, hence the join.
    """
    rows = con.execute(
        "SELECT p.fotmob_player_id, mode(p.player_name) AS player_name, "
        "       CASE p.league_id WHEN 47 THEN 'E0' WHEN 48 THEN 'E1' END AS competition, "
        "       p.season, mode(t.team_name) AS team_name "
        "FROM src_fotmob_player_stat p "
        "LEFT JOIN src_fotmob_team_stat t "
        "  ON t.league_id = p.league_id AND t.season = p.season "
        " AND t.fotmob_team_id = p.fotmob_team_id "
        "WHERE p.fotmob_player_id IS NOT NULL "
        "GROUP BY p.fotmob_player_id, p.league_id, p.season "
        "ORDER BY p.season, p.fotmob_player_id"
    ).fetchall()
    return _resolve_source_players(con, "fotmob", rows)


OVERRIDES_PATH = Path(__file__).with_name("player_overrides.yaml")


def apply_overrides(con: duckdb.DuckDBPyConnection, path=None) -> int:
    """Apply the committed manual corrections. Returns entries applied."""
    target = Path(path) if path is not None else OVERRIDES_PATH
    if not target.is_file():
        return 0

    data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    applied = 0

    for source, entries in data.items():
        for source_player_id, fpl_code in (entries or {}).items():
            key = str(source_player_id)
            con.execute(
                "DELETE FROM map_player_source WHERE source = ? AND source_player_id = ?",
                [source, key])
            con.execute(
                "DELETE FROM unresolved_player WHERE source = ? AND source_player_id = ?",
                [source, key])

            if fpl_code is None:
                applied += 1
                continue

            row = con.execute(
                "SELECT player_id FROM dim_player WHERE fpl_code = ?", [fpl_code]
            ).fetchone()
            if row is None:
                log.warning(
                    "override %s/%s references unknown fpl code %s", source, key, fpl_code)
                continue

            con.execute(
                "INSERT INTO map_player_source (player_id, source, source_player_id, "
                "method, confidence) VALUES (?, ?, ?, 'override', 1.0)",
                [row[0], source, key])
            applied += 1
    return applied


def resolve_players(con: duckdb.DuckDBPyConnection) -> int:
    """Rebuild player identity from the src_ tables. Returns spine matches.

    Every player table is wiped first and the sequence restarted, so two runs
    produce identical tables down to the player_ids. That is what makes the map
    re-derivable rather than an artefact that has to survive rebuilds.

    This runs once after every source has loaded, never per-source: matching is
    inherently cross-source, and the FPL spine must exist before anything can be
    matched into it.
    """
    con.execute("DELETE FROM map_player_source")
    con.execute("DELETE FROM unresolved_player")
    con.execute("DELETE FROM dim_player")
    con.execute("CREATE OR REPLACE SEQUENCE seq_player_id START 1")

    build_fpl_spine(con)
    matched = resolve_understat_players(con)
    matched += resolve_fotmob_players(con)
    apply_overrides(con)
    return matched
