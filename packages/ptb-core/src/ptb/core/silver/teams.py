"""Resolve a source's spelling of a club to one `dim_team` row."""
from __future__ import annotations

import functools
from pathlib import Path
from typing import Dict, Optional

import duckdb
import yaml

from .names import normalize_name

ALIASES_PATH = Path(__file__).with_name("team_aliases.yaml")


@functools.lru_cache(maxsize=1)
def load_aliases(path: Optional[Path] = None) -> Dict[str, str]:
    target = Path(path) if path is not None else ALIASES_PATH
    data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    return {normalize_name(k): v for k, v in data.items()}


def canonical_name(raw: str, aliases: Optional[Dict[str, str]] = None) -> str:
    aliases = load_aliases() if aliases is None else aliases
    return aliases.get(normalize_name(raw), raw.strip())


def resolve_team(
    con: duckdb.DuckDBPyConnection,
    raw_name: str,
    source: str,
    country: Optional[str] = None,
) -> int:
    """Find or create the team, and record this source's spelling of it."""
    canonical = canonical_name(raw_name)
    normalized = normalize_name(canonical)

    row = con.execute(
        "SELECT team_id FROM dim_team WHERE normalized_name = ?", [normalized]
    ).fetchone()

    if row is None:
        team_id = con.execute("SELECT nextval('seq_team_id')").fetchone()[0]
        con.execute(
            "INSERT INTO dim_team (team_id, canonical_name, normalized_name, country) "
            "VALUES (?, ?, ?, ?)",
            [team_id, canonical, normalized, country],
        )
    else:
        team_id = row[0]

    con.execute(
        "INSERT OR REPLACE INTO map_team_source (team_id, source, source_team_name) "
        "VALUES (?, ?, ?)",
        [team_id, source, raw_name],
    )
    return team_id
