"""Resolve a source's player to one `dim_player` row.

FPL's `code` is stable across seasons where `element_id` is reassigned, so it
forms the spine: one dim_player row per code. Understat and FotMob players are
then matched into that spine by three deterministic tiers and a scored fallback.

Where two candidates score too closely to separate, resolution fails and records
the reason instead of guessing -- the same contract as match resolution.
"""
from __future__ import annotations

import logging

import duckdb

from .text import normalize_name

log = logging.getLogger(__name__)

AMBIGUOUS = "ambiguous"


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
