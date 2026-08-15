"""Append-only archive of raw payloads -- the bronze layer.

This, not the warehouse, is the source of truth. Every payload is written
verbatim and never mutated, so a parser bug found in November is a re-parse
rather than a re-fetch. Payloads are self-contained -- each carries its own
competition, season and fetch metadata -- so replay never depends on the
order keys are read in.

A key looks like `{source}/{endpoint}/{label}__{stamp}.json.gz`. Only
`_path` knows how a key becomes a file, so moving the archive to object
storage later means replacing that one function: everything above here
speaks in keys and payloads, never paths.

`root` defaults to config.RAW_DIR and is resolved per call rather than at
import time, so a test can point one ingest at a temporary directory
without touching global state.
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
import re
from pathlib import Path
from typing import Any, List, Optional

from . import config

_STAMP_FORMAT = "%Y%m%dT%H%M%SZ"
_STAMP_RE = re.compile(r"(?:^|__)(\d{8}T\d{6}Z)\.json\.gz$")


def key_for(
    source: str,
    endpoint: str,
    captured_at: dt.datetime,
    label: Optional[str] = None,
) -> str:
    stamp = captured_at.strftime(_STAMP_FORMAT)
    name = "{}__{}".format(label, stamp) if label else stamp
    return "{}/{}/{}.json.gz".format(source, endpoint, name)


def stamp_of(key: str) -> dt.datetime:
    match = _STAMP_RE.search(key)
    if match is None:
        raise ValueError("not an archive key: {}".format(key))
    return dt.datetime.strptime(match.group(1), _STAMP_FORMAT)


def _root(root: Optional[Path]) -> Path:
    return Path(root) if root is not None else config.RAW_DIR


def _path(key: str, root: Optional[Path]) -> Path:
    base = _root(root).resolve()
    path = (base / key).resolve()
    if not path.is_relative_to(base):
        raise ValueError("key escapes archive root: {}".format(key))
    return path


def write(
    source: str,
    endpoint: str,
    payload: Any,
    label: Optional[str] = None,
    captured_at: Optional[dt.datetime] = None,
    root: Optional[Path] = None,
) -> str:
    captured_at = captured_at or dt.datetime.utcnow().replace(microsecond=0)
    key = key_for(source, endpoint, captured_at, label)
    blob = gzip.compress(json.dumps(payload, separators=(",", ":")).encode("utf-8"))

    path = _path(key, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write-then-rename, so an interrupted run never leaves a half-written
    # payload behind for a later rebuild to choke on.
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(blob)
    tmp.replace(path)
    return key


def read(key: str, root: Optional[Path] = None) -> Any:
    return json.loads(gzip.decompress(_path(key, root).read_bytes()).decode("utf-8"))


def exists(key: str, root: Optional[Path] = None) -> bool:
    return _path(key, root).is_file()


def keys(
    source: Optional[str] = None,
    endpoint: Optional[str] = None,
    root: Optional[Path] = None,
) -> List[str]:
    base = _root(root)
    if not base.is_dir():
        return []
    if source and endpoint:
        prefix = "{}/{}/".format(source, endpoint)
    elif source:
        prefix = "{}/".format(source)
    else:
        prefix = ""
    found = (
        str(path.relative_to(base)).replace("\\", "/")
        for path in base.rglob("*.json.gz")
        if path.is_file()
    )
    return sorted(key for key in found if key.startswith(prefix))
