"""Connect to the warehouse, and replay the archive into it.

The warehouse is derived and disposable: deleting the file and running
`ptb rebuild` must reproduce it exactly from the archive. Nothing in here
ever touches the network -- loaders read payloads the archive already holds.

Every loader is idempotent, so a full replay always produces the same
warehouse.
"""
from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path
from typing import Dict, List, Optional, Union

import duckdb

from . import archive, config
from .silver import SOURCES, players

log = logging.getLogger(__name__)

SCHEMA_DIR = Path(__file__).parent / "silver"


def _statements(sql: str):
    """Split one schema file into statements.

    DuckDB's Python execute() prepares a single statement, so each file has
    to be split on semicolons. Line comments are stripped first: a semicolon
    inside one is legal SQL and would otherwise cut a statement in half.
    This assumes no string literal in a schema file contains `--`, which is
    true and is the only thing that would mis-split.
    """
    uncommented = "\n".join(line.split("--", 1)[0] for line in sql.splitlines())
    for statement in uncommented.split(";"):
        statement = statement.strip()
        if statement:
            yield statement


def apply_schema(con: duckdb.DuckDBPyConnection) -> None:
    """Apply every silver module's DDL.

    Each `silver/*.sql` owns its own tables and none reference another's, so
    alphabetical order is enough and a new source needs no registration here.
    Every statement is `IF NOT EXISTS` or `INSERT OR REPLACE`, so this is safe
    to run against an existing warehouse on every connect.
    """
    for path in sorted(SCHEMA_DIR.glob("*.sql")):
        for statement in _statements(path.read_text(encoding="utf-8")):
            con.execute(statement)


def connect(
    path: Optional[Union[Path, str]] = None,
    read_only: bool = False,
) -> duckdb.DuckDBPyConnection:
    target = Path(path) if path is not None else config.DB_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(target), read_only=read_only)
    if not read_only:
        apply_schema(con)
    return con


def is_loaded(con: duckdb.DuckDBPyConnection, key: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM meta_archive_loaded WHERE archive_key = ?", [key]
    ).fetchone()
    return row is not None


def mark_loaded(con: duckdb.DuckDBPyConnection, key: str) -> None:
    con.execute(
        "INSERT OR REPLACE INTO meta_archive_loaded (archive_key, source, loaded_at) "
        "VALUES (?, ?, ?)",
        [key, key.split("/", 1)[0], dt.datetime.utcnow()],
    )


def rebuild(
    con: duckdb.DuckDBPyConnection,
    sources: Optional[List[str]] = None,
    root: Optional[Path] = None,
) -> Dict[str, int]:
    """Replay the archive. Returns rows written per source."""
    written: Dict[str, int] = {}

    for key in archive.keys(root=root):
        source = key.split("/", 1)[0]
        if sources and source not in sources:
            continue
        module = SOURCES.get(source)
        if module is None:
            log.debug("no silver module for source %s, skipping %s", source, key)
            continue

        try:
            payload = archive.read(key, root=root)
        except Exception:
            log.warning("unreadable payload, skipping: %s", key, exc_info=True)
            continue

        try:
            rows = module.load(con, payload, key)
        except Exception:
            # The payload stays in the archive for a later, fixed parser.
            log.warning("loader failed, skipping: %s", key, exc_info=True)
            continue

        mark_loaded(con, key)
        written[source] = written.get(source, 0) + rows

    # Match identity is per-source and incremental, so only sources that
    # actually loaded something are re-resolved. Sources with no fixtures to
    # offer (FotMob's stat tables, vaastav's backfill) define no resolver.
    for source in sorted(written):
        resolve = getattr(SOURCES[source], "resolve_matches", None)
        if resolve is not None:
            log.info("resolved %d %s matches into dim_match", resolve(con), source)

    # Player matching is inherently cross-source -- an Understat player is
    # matched against the FPL spine -- so it runs once after every source has
    # loaded, not inside the per-source loop above.
    if written:
        log.info("matched %d source players into dim_player", players.resolve_players(con))

    return written
