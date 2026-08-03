"""Connection handling and schema application.

The warehouse is derived and disposable: deleting the file and running
`ptb rebuild` must reproduce it exactly from the archive.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

import duckdb

from .. import config

SCHEMA_SQL = Path(__file__).with_name("schema.sql")


def apply_schema(con: duckdb.DuckDBPyConnection) -> None:
    # DuckDB's Python execute() prepares a single statement, so the schema is
    # split and applied one statement at a time. No value in schema.sql
    # contains a semicolon, which keeps the split safe.
    sql = SCHEMA_SQL.read_text(encoding="utf-8")
    for statement in (s.strip() for s in sql.split(";")):
        if statement:
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
