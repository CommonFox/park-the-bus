"""Append-only archive of raw payloads.

This, not the warehouse, is the source of truth. Every payload is written
verbatim and never mutated, so a parser bug found in November is a re-parse
rather than a re-fetch. Payloads are self-contained -- each carries its own
competition, season and fetch metadata -- so replay never depends on the
order keys are read in.
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
import re
from typing import Any, Dict, List, Optional

from .. import config
from .backends import ArchiveBackend, LocalBackend

_STAMP_FORMAT = "%Y%m%dT%H%M%SZ"
_STAMP_RE = re.compile(r"(?:^|__)(\d{8}T\d{6}Z)\.json\.gz$")


class RawArchive:
    def __init__(self, backend: Optional[ArchiveBackend] = None) -> None:
        self._backend = backend if backend is not None else LocalBackend(config.RAW_DIR)

    @staticmethod
    def key_for(
        source: str,
        endpoint: str,
        captured_at: dt.datetime,
        label: Optional[str] = None,
    ) -> str:
        stamp = captured_at.strftime(_STAMP_FORMAT)
        name = "{}__{}".format(label, stamp) if label else stamp
        return "{}/{}/{}.json.gz".format(source, endpoint, name)

    def write(
        self,
        source: str,
        endpoint: str,
        payload: Any,
        label: Optional[str] = None,
        captured_at: Optional[dt.datetime] = None,
    ) -> str:
        captured_at = captured_at or dt.datetime.utcnow().replace(microsecond=0)
        key = self.key_for(source, endpoint, captured_at, label)
        blob = gzip.compress(
            json.dumps(payload, separators=(",", ":")).encode("utf-8")
        )
        self._backend.write_bytes(key, blob)
        return key

    def read(self, key: str) -> Any:
        return json.loads(gzip.decompress(self._backend.read_bytes(key)).decode("utf-8"))

    def exists(self, key: str) -> bool:
        return self._backend.exists(key)

    def keys(self, source: Optional[str] = None, endpoint: Optional[str] = None) -> List[str]:
        if source and endpoint:
            prefix = "{}/{}/".format(source, endpoint)
        elif source:
            prefix = "{}/".format(source)
        else:
            prefix = ""
        return self._backend.list_keys(prefix)

    @staticmethod
    def stamp_of(key: str) -> dt.datetime:
        match = _STAMP_RE.search(key)
        if match is None:
            raise ValueError("not an archive key: {}".format(key))
        return dt.datetime.strptime(match.group(1), _STAMP_FORMAT)
