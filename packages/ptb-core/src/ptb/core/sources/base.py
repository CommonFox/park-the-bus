"""What every data provider must look like.

A source's only job is to fetch and archive. It never writes to the
warehouse -- that is the loader's job, and keeping them apart is what makes
a re-parse cheap.
"""
from __future__ import annotations

from typing import List, Protocol, runtime_checkable

from ..archive import RawArchive


@runtime_checkable
class Source(Protocol):
    name: str

    def ingest(self, archive: RawArchive, **options) -> List[str]:
        """Fetch from the provider, write to the archive, return written keys."""
        ...
