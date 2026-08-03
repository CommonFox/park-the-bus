"""Replay archived payloads into the warehouse.

Every loader is idempotent, so a full replay always produces the same
warehouse. Loaders never fetch; this module never touches the network.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Callable, Dict, List, Optional

import duckdb

from ..archive import RawArchive

log = logging.getLogger(__name__)

# source name -> (con, payload, archive_key) -> rows written
LOADERS: Dict[str, Callable[[duckdb.DuckDBPyConnection, dict, str], int]] = {}


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
    archive: RawArchive,
    sources: Optional[List[str]] = None,
) -> Dict[str, int]:
    """Replay the archive. Returns rows written per source."""
    written: Dict[str, int] = {}

    for key in archive.keys():
        source = key.split("/", 1)[0]
        if sources and source not in sources:
            continue
        loader = LOADERS.get(source)
        if loader is None:
            log.debug("no loader for source %s, skipping %s", source, key)
            continue

        try:
            payload = archive.read(key)
        except Exception:
            log.warning("unreadable payload, skipping: %s", key, exc_info=True)
            continue

        try:
            rows = loader(con, payload, key)
        except Exception:
            # The payload stays in the archive for a later, fixed parser.
            log.warning("loader failed, skipping: %s", key, exc_info=True)
            continue

        mark_loaded(con, key)
        written[source] = written.get(source, 0) + rows

    return written
