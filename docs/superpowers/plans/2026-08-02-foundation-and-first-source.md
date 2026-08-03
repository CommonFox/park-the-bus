# Foundation and First Source — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the `park-the-bus` workspace with a keyed raw archive, a pluggable source layer, and a DuckDB warehouse — proven end to end by ingesting football-data.co.uk for the Big 5 back to 1993/94.

**Architecture:** An append-only raw archive is the source of truth; DuckDB is derived and disposable. `ptb ingest` fetches to the archive and never writes DuckDB; `ptb rebuild` replays the archive into DuckDB and never touches the network. Warehouse tables are source-faithful (`src_*`), with conformed entities (`dim_*`) and identity bridges (`map_*`) layered on top.

**Tech Stack:** Python 3.12, `uv` workspace, DuckDB, `requests`, `pytest`.

**Spec:** `docs/superpowers/specs/2026-08-02-universal-soccer-data-layer-design.md`

## Global Constraints

- Python `>=3.12`. The workspace root pins `requires-python = ">=3.12"`.
- Seasons are stored as `2024/25` everywhere. Sources using a start year (`2425`, `2024`) convert at load time.
- `RawArchive`'s public API takes and returns **string keys**, never `pathlib.Path`. A future object-storage backend must drop in without changing callers.
- Fetching and loading never occur in the same command. `ingest` writes only to the archive; `rebuild` reads only from it.
- Every loader is idempotent: loading the same archive key twice leaves the warehouse unchanged.
- Nothing is averaged or merged across sources. `src_*` tables stay source-faithful.
- Archived payloads are verbatim and self-contained — each carries its own competition, season, and fetch metadata.
- `data/` is gitignored. Never commit the archive or the `.duckdb` file.
- Identity resolution failures produce recorded unresolved rows, never dropped data.

---

## File Structure

| File | Responsibility |
|---|---|
| `pyproject.toml` | uv workspace root; member list, shared dev deps |
| `packages/ptb-core/pyproject.toml` | `ptb-core` package metadata, deps, `ptb` entry point |
| `.../ptb/core/config.py` | Paths and tunables resolved from env |
| `.../ptb/core/cli.py` | Argument parsing and subcommand dispatch |
| `.../ptb/core/archive/backends.py` | `ArchiveBackend` protocol + `LocalBackend` |
| `.../ptb/core/archive/archive.py` | `RawArchive` — key construction, envelope read/write |
| `.../ptb/core/sources/base.py` | `Source` protocol and `SourceResult` |
| `.../ptb/core/sources/registry.py` | `register_source` / `get_source` / `list_sources` |
| `.../ptb/core/sources/footballdata.py` | football-data.co.uk fetch + season/competition codes |
| `.../ptb/core/warehouse/schema.sql` | All DDL for this plan |
| `.../ptb/core/warehouse/db.py` | Connection handling, schema application |
| `.../ptb/core/warehouse/load.py` | Loader registry and `rebuild()` |
| `.../ptb/core/warehouse/loaders/footballdata.py` | CSV envelope → `src_footballdata_match` |
| `.../ptb/core/identity/text.py` | Name normalization (diacritics, punctuation) |
| `.../ptb/core/identity/teams.py` | Alias table + `dim_team` / `map_team_source` |
| `.../ptb/core/identity/team_aliases.yaml` | Committed, hand-maintained alias map |
| `.../ptb/core/identity/matches.py` | `dim_match` resolution with ±36h window |

---

### Task 1: Workspace skeleton and CLI shell

**Files:**
- Create: `pyproject.toml`, `.gitignore`
- Create: `packages/ptb-core/pyproject.toml`
- Create: `packages/ptb-core/src/ptb/core/__init__.py`, `config.py`, `cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: nothing
- Produces: `ptb.core.config.DATA_DIR: Path`, `RAW_DIR: Path`, `DB_PATH: Path`; `ptb.core.cli.main(argv: list[str] | None = None) -> int`

- [ ] **Step 1: Install uv**

`uv` is not present on this machine. Install it and confirm:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
uv --version
```

- [ ] **Step 2: Create the workspace root**

`pyproject.toml`:

```toml
[project]
name = "park-the-bus"
version = "0.1.0"
description = "Personal soccer analytics monorepo"
requires-python = ">=3.12"
dependencies = ["ptb-core"]

[tool.uv]
package = false

[tool.uv.workspace]
members = ["packages/*"]

[tool.uv.sources]
ptb-core = { workspace = true }

[dependency-groups]
dev = ["pytest>=8.0", "pytest-cov>=5.0"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

`.gitignore`:

```
__pycache__/
*.py[cod]
.pytest_cache/
.venv/
*.egg-info/
build/
dist/

# Archive and warehouse are rebuildable / large — keep them out of git.
data/

# Secrets — never commit.
.env
.env.*
!.env.example
```

- [ ] **Step 3: Create the ptb-core package**

`packages/ptb-core/pyproject.toml`:

```toml
[project]
name = "ptb-core"
version = "0.1.0"
description = "Universal soccer data collection and warehouse"
requires-python = ">=3.12"
dependencies = [
    "duckdb>=1.1",
    "requests>=2.32",
    "pyyaml>=6.0",
]

[project.scripts]
ptb = "ptb.core.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/ptb"]
```

Create `packages/ptb-core/src/ptb/core/__init__.py` containing only:

```python
__version__ = "0.1.0"
```

Note: do **not** create `src/ptb/__init__.py`. `ptb` is a namespace package so later
workspace members (`ptb-gar`, `ptb-fpl`) can add `ptb.gar`, `ptb.fpl` alongside it.

- [ ] **Step 4: Write the failing test**

`tests/test_cli.py`:

```python
import pytest

from ptb.core import cli


def test_version_flag_prints_version(capsys):
    exit_code = cli.main(["--version"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "0.1.0" in captured.out


def test_no_command_prints_usage_and_fails(capsys):
    exit_code = cli.main([])
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "usage" in captured.out.lower()


def test_unknown_command_exits_nonzero():
    with pytest.raises(SystemExit):
        cli.main(["nonsense-command"])
```

- [ ] **Step 5: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ptb.core.cli'`

- [ ] **Step 6: Write config.py**

`packages/ptb-core/src/ptb/core/config.py`:

```python
"""Paths and tunables. Everything is overridable by environment variable so
tests can point the archive and warehouse at a temporary directory."""
from __future__ import annotations

import os
from pathlib import Path


def _path_from_env(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value) if value else default


# config.py sits at packages/ptb-core/src/ptb/core/, so the repo root is six
# levels up: core -> ptb -> src -> ptb-core -> packages -> repo.
REPO_ROOT = Path(__file__).resolve().parents[5]
DATA_DIR = _path_from_env("PTB_DATA_DIR", REPO_ROOT / "data")
RAW_DIR = _path_from_env("PTB_RAW_DIR", DATA_DIR / "raw")
DB_PATH = _path_from_env("PTB_DB_PATH", DATA_DIR / "ptb.duckdb")

USER_AGENT = os.environ.get(
    "PTB_USER_AGENT",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36",
)
REQUEST_TIMEOUT = float(os.environ.get("PTB_REQUEST_TIMEOUT", "30"))
```

- [ ] **Step 7: Write cli.py**

`packages/ptb-core/src/ptb/core/cli.py`:

```python
"""The `ptb` command.

Subcommands are added by later tasks. The split that matters: `ingest` writes
only to the archive, `rebuild` reads only from it. Nothing does both.
"""
from __future__ import annotations

import argparse
import logging
import sys
from typing import Optional, Sequence

from . import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ptb", description="Soccer data collection and warehouse")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    parser.add_subparsers(dest="command", metavar="<command>")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    if not args.command:
        parser.print_usage()
        return 2

    handler = _HANDLERS.get(args.command)
    if handler is None:
        parser.error("unknown command: {}".format(args.command))
    return handler(args)


_HANDLERS: dict = {}


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -v`
Expected: 3 passed

Also confirm the console script works: `uv run ptb --version` prints `0.1.0`.

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml .gitignore packages/ tests/
git commit -m "Set up the uv workspace and the ptb command"
```

---

### Task 2: Raw archive with a swappable local backend

**Files:**
- Create: `packages/ptb-core/src/ptb/core/archive/__init__.py`, `backends.py`, `archive.py`
- Test: `tests/test_archive.py`

**Interfaces:**
- Consumes: `ptb.core.config.RAW_DIR`
- Produces:
  - `ArchiveBackend` protocol: `write_bytes(key: str, data: bytes) -> None`, `read_bytes(key: str) -> bytes`, `exists(key: str) -> bool`, `list_keys(prefix: str) -> list[str]`
  - `LocalBackend(root: Path)`
  - `RawArchive(backend: ArchiveBackend | None = None)` with `write(source: str, endpoint: str, payload: dict, label: str | None = None, captured_at: datetime | None = None) -> str` (returns the key), `read(key: str) -> dict`, `keys(source: str | None = None, endpoint: str | None = None) -> list[str]`, `stamp_of(key: str) -> datetime`

- [ ] **Step 1: Write the failing test**

`tests/test_archive.py`:

```python
import datetime as dt

import pytest

from ptb.core.archive import LocalBackend, RawArchive


@pytest.fixture
def archive(tmp_path):
    return RawArchive(LocalBackend(tmp_path))


def test_write_returns_key_and_roundtrips(archive):
    stamp = dt.datetime(2026, 8, 2, 10, 15, 0)
    key = archive.write("footballdata", "season", {"csv": "a,b\n1,2\n"},
                        label="E0__2024-25", captured_at=stamp)

    assert key == "footballdata/season/E0__2024-25__20260802T101500Z.json.gz"
    assert archive.read(key) == {"csv": "a,b\n1,2\n"}


def test_key_omits_label_when_absent(archive):
    stamp = dt.datetime(2026, 8, 2, 10, 15, 0)
    key = archive.write("fotmob", "matches", {"x": 1}, captured_at=stamp)
    assert key == "fotmob/matches/20260802T101500Z.json.gz"


def test_keys_filters_by_source_and_endpoint(archive):
    stamp = dt.datetime(2026, 8, 2, 10, 15, 0)
    archive.write("footballdata", "season", {"n": 1}, label="E0", captured_at=stamp)
    archive.write("footballdata", "season", {"n": 2}, label="SP1", captured_at=stamp)
    archive.write("fotmob", "matches", {"n": 3}, captured_at=stamp)

    assert len(archive.keys()) == 3
    assert len(archive.keys(source="footballdata")) == 2
    assert len(archive.keys(source="footballdata", endpoint="season")) == 2
    assert len(archive.keys(source="fotmob")) == 1


def test_keys_are_returned_sorted(archive):
    early = dt.datetime(2026, 8, 1, 9, 0, 0)
    late = dt.datetime(2026, 8, 3, 9, 0, 0)
    archive.write("footballdata", "season", {"n": 2}, label="E0", captured_at=late)
    archive.write("footballdata", "season", {"n": 1}, label="E0", captured_at=early)

    keys = archive.keys(source="footballdata")
    assert keys == sorted(keys)
    assert archive.read(keys[0]) == {"n": 1}


def test_stamp_of_recovers_capture_time(archive):
    stamp = dt.datetime(2026, 8, 2, 10, 15, 0)
    key = archive.write("footballdata", "season", {"n": 1}, label="E0", captured_at=stamp)
    assert archive.stamp_of(key) == stamp


def test_stamp_of_rejects_a_non_archive_key(archive):
    with pytest.raises(ValueError):
        archive.stamp_of("footballdata/season/not-a-stamp.json.gz")


def test_public_api_takes_no_paths(archive):
    """The archive is addressed by key so an object-store backend can drop in."""
    stamp = dt.datetime(2026, 8, 2, 10, 15, 0)
    key = archive.write("footballdata", "season", {"n": 1}, captured_at=stamp)
    assert isinstance(key, str)
    assert all(isinstance(k, str) for k in archive.keys())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_archive.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ptb.core.archive'`

- [ ] **Step 3: Write backends.py**

`packages/ptb-core/src/ptb/core/archive/backends.py`:

```python
"""Storage backends for the raw archive.

Only the local filesystem is implemented. The protocol exists so an
object-store backend (R2, B2) can be added later without touching callers:
everything above this layer speaks in string keys and bytes.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Protocol


class ArchiveBackend(Protocol):
    def write_bytes(self, key: str, data: bytes) -> None: ...
    def read_bytes(self, key: str) -> bytes: ...
    def exists(self, key: str) -> bool: ...
    def list_keys(self, prefix: str = "") -> List[str]: ...


class LocalBackend:
    """Keys map to paths under `root`. That mapping is this class's private
    business — no caller should ever see a Path."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def _resolve(self, key: str) -> Path:
        path = (self._root / key).resolve()
        root = self._root.resolve()
        if not path.is_relative_to(root):
            raise ValueError("key escapes archive root: {}".format(key))
        return path

    def write_bytes(self, key: str, data: bytes) -> None:
        path = self._resolve(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)

    def read_bytes(self, key: str) -> bytes:
        return self._resolve(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._resolve(key).is_file()

    def list_keys(self, prefix: str = "") -> List[str]:
        root = self._root
        if not root.is_dir():
            return []
        keys = [
            str(p.relative_to(root)).replace("\\", "/")
            for p in root.rglob("*.json.gz")
            if p.is_file()
        ]
        return sorted(k for k in keys if k.startswith(prefix))
```

Note the atomic write: a crash mid-write leaves no half-file in the archive.

- [ ] **Step 4: Write archive.py**

`packages/ptb-core/src/ptb/core/archive/archive.py`:

```python
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
```

`packages/ptb-core/src/ptb/core/archive/__init__.py`:

```python
from .archive import RawArchive
from .backends import ArchiveBackend, LocalBackend

__all__ = ["RawArchive", "ArchiveBackend", "LocalBackend"]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_archive.py -v`
Expected: 7 passed

- [ ] **Step 6: Commit**

```bash
git add packages/ptb-core/src/ptb/core/archive/ tests/test_archive.py
git commit -m "Add the keyed raw archive and its local backend"
```

---

### Task 3: Source protocol and registry

**Files:**
- Create: `packages/ptb-core/src/ptb/core/sources/__init__.py`, `base.py`, `registry.py`
- Test: `tests/test_source_registry.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `Source` protocol with `name: str` and `ingest(archive: RawArchive, **options) -> list[str]` (returns written keys)
  - `register_source(cls)` decorator, `get_source(name: str) -> Source`, `list_sources() -> list[str]`
  - `UnknownSourceError(KeyError)`

- [ ] **Step 1: Write the failing test**

`tests/test_source_registry.py`:

```python
import pytest

from ptb.core.sources import registry


@pytest.fixture(autouse=True)
def clean_registry():
    saved = dict(registry._REGISTRY)
    registry._REGISTRY.clear()
    yield
    registry._REGISTRY.clear()
    registry._REGISTRY.update(saved)


def test_register_and_get_a_source():
    @registry.register_source
    class Dummy:
        name = "dummy"

        def ingest(self, archive, **options):
            return []

    assert isinstance(registry.get_source("dummy"), Dummy)
    assert registry.list_sources() == ["dummy"]


def test_list_sources_is_sorted():
    for source_name in ("zulu", "alpha", "mike"):
        @registry.register_source
        class _S:
            name = source_name

            def ingest(self, archive, **options):
                return []

    assert registry.list_sources() == ["alpha", "mike", "zulu"]


def test_unknown_source_raises_with_available_names():
    @registry.register_source
    class Dummy:
        name = "dummy"

        def ingest(self, archive, **options):
            return []

    with pytest.raises(registry.UnknownSourceError) as excinfo:
        registry.get_source("nope")
    assert "dummy" in str(excinfo.value)


def test_duplicate_registration_is_rejected():
    @registry.register_source
    class First:
        name = "dupe"

        def ingest(self, archive, **options):
            return []

    with pytest.raises(ValueError):
        @registry.register_source
        class Second:
            name = "dupe"

            def ingest(self, archive, **options):
                return []


def test_source_without_a_name_is_rejected():
    with pytest.raises(ValueError):
        @registry.register_source
        class Nameless:
            def ingest(self, archive, **options):
                return []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_source_registry.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ptb.core.sources'`

- [ ] **Step 3: Write base.py**

`packages/ptb-core/src/ptb/core/sources/base.py`:

```python
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
```

- [ ] **Step 4: Write registry.py**

`packages/ptb-core/src/ptb/core/sources/registry.py`:

```python
"""Name -> source lookup, so the CLI can dispatch on a string."""
from __future__ import annotations

from typing import Dict, List, Type

_REGISTRY: Dict[str, Type] = {}


class UnknownSourceError(KeyError):
    pass


def register_source(cls: Type) -> Type:
    name = getattr(cls, "name", None)
    if not name or not isinstance(name, str):
        raise ValueError("{} needs a non-empty string `name`".format(cls.__name__))
    if name in _REGISTRY:
        raise ValueError("source {!r} is already registered".format(name))
    _REGISTRY[name] = cls
    return cls


def get_source(name: str):
    try:
        return _REGISTRY[name]()
    except KeyError:
        raise UnknownSourceError(
            "unknown source {!r}; available: {}".format(name, ", ".join(list_sources()))
        ) from None


def list_sources() -> List[str]:
    return sorted(_REGISTRY)
```

`packages/ptb-core/src/ptb/core/sources/__init__.py`:

```python
from .base import Source
from .registry import UnknownSourceError, get_source, list_sources, register_source

__all__ = ["Source", "register_source", "get_source", "list_sources", "UnknownSourceError"]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_source_registry.py -v`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add packages/ptb-core/src/ptb/core/sources/ tests/test_source_registry.py
git commit -m "Add the source protocol and registry"
```

---

### Task 4: football-data.co.uk source and `ptb ingest`

**Files:**
- Create: `packages/ptb-core/src/ptb/core/sources/footballdata.py`
- Modify: `packages/ptb-core/src/ptb/core/sources/__init__.py` (import for registration side effect)
- Modify: `packages/ptb-core/src/ptb/core/cli.py` (add the `ingest` subcommand)
- Test: `tests/test_footballdata_source.py`

**Interfaces:**
- Consumes: `RawArchive.write`, `register_source`
- Produces:
  - `season_code(2024) -> "2425"`, `season_label(2024) -> "2024/25"`, `parse_season("2024/25") -> 2024`
  - `COMPETITIONS: dict[str, dict]` — code to country/name/tier
  - `BIG_5: tuple[str, ...]` = `("E0", "SP1", "I1", "D1", "F1")`
  - `FootballDataSource.ingest(archive, competitions=..., seasons=...) -> list[str]`
  - Envelope shape: `{"source", "competition", "season", "url", "fetched_at", "csv"}`

- [ ] **Step 1: Write the failing test**

`tests/test_footballdata_source.py`:

```python
import datetime as dt

import pytest

from ptb.core.archive import LocalBackend, RawArchive
from ptb.core.sources import footballdata as fd


def test_season_code_maps_start_year_to_four_digits():
    assert fd.season_code(2024) == "2425"
    assert fd.season_code(1993) == "9394"
    assert fd.season_code(1999) == "9900"
    assert fd.season_code(2000) == "0001"


def test_season_label_uses_the_warehouse_convention():
    assert fd.season_label(2024) == "2024/25"
    assert fd.season_label(1993) == "1993/94"
    assert fd.season_label(1999) == "1999/00"


def test_parse_season_is_the_inverse_of_season_label():
    for year in (1993, 1999, 2000, 2024):
        assert fd.parse_season(fd.season_label(year)) == year


def test_big_5_codes_are_all_known_competitions():
    for code in fd.BIG_5:
        assert code in fd.COMPETITIONS


def test_decode_strips_the_utf8_bom():
    """Every football-data file starts with EF BB BF, which would otherwise
    turn the first column name into '\\ufeffDiv' and break lookups."""
    raw = b"\xef\xbb\xbfDiv,Date\nE0,16/08/2024\n"
    assert fd.decode_csv(raw).startswith("Div,Date")


def test_decode_falls_back_to_latin1_for_stray_bytes():
    """Older files are latin-1 and carry accented referee names."""
    raw = "Div,Referee\nE0,M Ju\xe1rez\n".encode("latin-1")
    assert "Ju\xe1rez" in fd.decode_csv(raw)


def test_ingest_archives_one_envelope_per_competition_season(tmp_path, monkeypatch):
    archive = RawArchive(LocalBackend(tmp_path))
    monkeypatch.setattr(fd, "_fetch_csv", lambda url: "Div,Date\nE0,16/08/2024\n")

    keys = fd.FootballDataSource().ingest(
        archive,
        competitions=["E0", "SP1"],
        seasons=[2024],
        captured_at=dt.datetime(2026, 8, 2, 10, 15, 0),
    )

    assert keys == [
        "footballdata/season/E0__2024-25__20260802T101500Z.json.gz",
        "footballdata/season/SP1__2024-25__20260802T101500Z.json.gz",
    ]
    envelope = archive.read(keys[0])
    assert envelope["source"] == "footballdata"
    assert envelope["competition"] == "E0"
    assert envelope["season"] == "2024/25"
    assert envelope["csv"].startswith("Div,Date")
    assert "mmz4281/2425/E0.csv" in envelope["url"]


def test_ingest_skips_a_season_the_site_has_not_published(tmp_path, monkeypatch):
    archive = RawArchive(LocalBackend(tmp_path))

    def fake_fetch(url):
        if "2526" in url:
            raise fd.NotPublishedError(url)
        return "Div,Date\nE0,16/08/2024\n"

    monkeypatch.setattr(fd, "_fetch_csv", fake_fetch)

    keys = fd.FootballDataSource().ingest(
        archive, competitions=["E0"], seasons=[2024, 2025],
        captured_at=dt.datetime(2026, 8, 2, 10, 15, 0),
    )

    assert len(keys) == 1
    assert "2024-25" in keys[0]


def test_ingest_rejects_an_unknown_competition(tmp_path):
    archive = RawArchive(LocalBackend(tmp_path))
    with pytest.raises(ValueError, match="ZZ9"):
        fd.FootballDataSource().ingest(archive, competitions=["ZZ9"], seasons=[2024])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_footballdata_source.py -v`
Expected: FAIL — `ImportError: cannot import name 'footballdata'`

- [ ] **Step 3: Write footballdata.py**

`packages/ptb-core/src/ptb/core/sources/footballdata.py`:

```python
"""football-data.co.uk -- results, match statistics and bookmaker odds.

Free, no key: one CSV per competition-season at /mmz4281/{code}/{comp}.csv,
back to 1993/94. Static files, so this is the one source here with no
scraping fragility -- which is why it goes first.

Column availability varies enormously by era: 1993/94 has 28 columns and no
match statistics at all, 2000/01 has 45 including shots but no kickoff time,
2024/25 has 120. Even within a season the count differs between
competitions. Nothing here may assume a fixed schema; the loader reads by
column name and tolerates absence.
"""
from __future__ import annotations

import datetime as dt
import logging
import time
from typing import Dict, List, Optional, Sequence

import requests

from .. import config
from ..archive import RawArchive
from .registry import register_source

log = logging.getLogger(__name__)

BASE = "https://www.football-data.co.uk/mmz4281"

COMPETITIONS: Dict[str, Dict[str, object]] = {
    "E0": {"country": "England", "name": "Premier League", "tier": 1},
    "E1": {"country": "England", "name": "Championship", "tier": 2},
    "SP1": {"country": "Spain", "name": "La Liga", "tier": 1},
    "SP2": {"country": "Spain", "name": "Segunda Division", "tier": 2},
    "I1": {"country": "Italy", "name": "Serie A", "tier": 1},
    "I2": {"country": "Italy", "name": "Serie B", "tier": 2},
    "D1": {"country": "Germany", "name": "Bundesliga", "tier": 1},
    "D2": {"country": "Germany", "name": "2. Bundesliga", "tier": 2},
    "F1": {"country": "France", "name": "Ligue 1", "tier": 1},
    "F2": {"country": "France", "name": "Ligue 2", "tier": 2},
}

BIG_5 = ("E0", "SP1", "I1", "D1", "F1")

_POLITENESS_SECONDS = 1.0


class NotPublishedError(RuntimeError):
    """football-data has no file for this competition-season yet."""


def season_code(start_year: int) -> str:
    """2024 -> '2425', 1999 -> '9900'."""
    return "{:02d}{:02d}".format(start_year % 100, (start_year + 1) % 100)


def season_label(start_year: int) -> str:
    """2024 -> '2024/25'. The warehouse convention, used everywhere."""
    return "{}/{:02d}".format(start_year, (start_year + 1) % 100)


def parse_season(label: str) -> int:
    """'2024/25' -> 2024."""
    return int(label.split("/")[0])


def decode_csv(raw: bytes) -> str:
    """football-data files carry a UTF-8 BOM, and the older ones are latin-1
    with occasional stray bytes. utf-8-sig handles the BOM; latin-1 never
    fails, so it is a safe last resort."""
    for encoding in ("utf-8-sig", "latin-1"):
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        return text.lstrip("﻿")
    raise ValueError("could not decode payload")


def _fetch_csv(url: str) -> str:
    session = requests.Session()
    session.headers.update({"User-Agent": config.USER_AGENT})
    response = session.get(url, timeout=config.REQUEST_TIMEOUT)
    if response.status_code == 404:
        raise NotPublishedError(url)
    response.raise_for_status()
    if not response.content.strip():
        raise NotPublishedError(url)
    return decode_csv(response.content)


@register_source
class FootballDataSource:
    name = "footballdata"

    def ingest(
        self,
        archive: RawArchive,
        competitions: Optional[Sequence[str]] = None,
        seasons: Optional[Sequence[int]] = None,
        captured_at: Optional[dt.datetime] = None,
        **_ignored,
    ) -> List[str]:
        competitions = list(competitions or BIG_5)
        seasons = list(seasons or [dt.date.today().year - (0 if dt.date.today().month >= 7 else 1)])
        captured_at = captured_at or dt.datetime.utcnow().replace(microsecond=0)

        unknown = [c for c in competitions if c not in COMPETITIONS]
        if unknown:
            raise ValueError("unknown competition(s): {}".format(", ".join(unknown)))

        written: List[str] = []
        for season in seasons:
            for competition in competitions:
                url = "{}/{}/{}.csv".format(BASE, season_code(season), competition)
                try:
                    csv_text = _fetch_csv(url)
                except NotPublishedError:
                    log.info("not published yet: %s %s", competition, season_label(season))
                    continue

                envelope = {
                    "source": self.name,
                    "competition": competition,
                    "season": season_label(season),
                    "url": url,
                    "fetched_at": captured_at.isoformat() + "Z",
                    "csv": csv_text,
                }
                key = archive.write(
                    self.name,
                    "season",
                    envelope,
                    label="{}__{}".format(competition, season_label(season).replace("/", "-")),
                    captured_at=captured_at,
                )
                written.append(key)
                log.info("archived %s %s", competition, season_label(season))
                time.sleep(_POLITENESS_SECONDS)

        return written
```

Note `season_label` uses `{:02d}` so 1999 gives `1999/00`, not `1999/0`.

- [ ] **Step 4: Register the module and add the `ingest` command**

Append to `packages/ptb-core/src/ptb/core/sources/__init__.py`:

```python
from . import footballdata  # noqa: F401  -- imported for registration side effect
```

In `packages/ptb-core/src/ptb/core/cli.py`, replace `parser.add_subparsers(...)` in
`build_parser` with:

```python
    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    ingest = subparsers.add_parser("ingest", help="fetch from a source into the archive")
    ingest.add_argument("source", help="source name, or 'all'")
    ingest.add_argument("--competition", help="comma-separated competition codes")
    ingest.add_argument("--season", help="comma-separated seasons, e.g. 2024/25,2023/24")

    return parser
```

and add the handler plus registration, replacing the empty `_HANDLERS: dict = {}`:

```python
def _cmd_ingest(args) -> int:
    from .archive import RawArchive
    from .sources import get_source, list_sources
    from .sources.footballdata import parse_season

    names = list_sources() if args.source == "all" else [args.source]
    archive = RawArchive()

    options = {}
    if args.competition:
        options["competitions"] = [c.strip() for c in args.competition.split(",")]
    if args.season:
        options["seasons"] = [parse_season(s.strip()) for s in args.season.split(",")]

    total = 0
    for name in names:
        keys = get_source(name).ingest(archive, **options)
        print("{}: archived {} payload(s)".format(name, len(keys)))
        total += len(keys)

    return 0 if total else 1


_HANDLERS = {"ingest": _cmd_ingest}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_footballdata_source.py -v`
Expected: 9 passed

- [ ] **Step 6: Verify against the live site**

This is the one task where a real network check is worth it, because the whole
plan rests on this URL shape holding:

```bash
uv run ptb ingest footballdata --competition E0 --season 2024/25
```

Expected: `footballdata: archived 1 payload(s)`, and a file appears under
`data/raw/footballdata/season/`.

- [ ] **Step 7: Commit**

```bash
git add packages/ptb-core/src/ptb/core/sources/ packages/ptb-core/src/ptb/core/cli.py tests/test_footballdata_source.py
git commit -m "Ingest football-data.co.uk into the archive"
```

---

### Task 5: DuckDB warehouse skeleton and `ptb rebuild`

**Files:**
- Create: `packages/ptb-core/src/ptb/core/warehouse/__init__.py`, `db.py`, `schema.sql`
- Create: `packages/ptb-core/src/ptb/core/warehouse/load.py`
- Modify: `packages/ptb-core/src/ptb/core/cli.py` (add `rebuild`)
- Test: `tests/test_warehouse_db.py`

**Interfaces:**
- Consumes: `config.DB_PATH`, `RawArchive.keys`
- Produces:
  - `connect(path: Path | str | None = None, read_only: bool = False) -> duckdb.DuckDBPyConnection` (applies schema)
  - `apply_schema(con) -> None`
  - `LOADERS: dict[str, Callable[[con, dict, str], int]]` keyed by source name
  - `rebuild(con, archive, sources: list[str] | None = None) -> dict[str, int]`
  - `is_loaded(con, key) -> bool`, `mark_loaded(con, key) -> None`

- [ ] **Step 1: Write the failing test**

`tests/test_warehouse_db.py`:

```python
import datetime as dt

import pytest

from ptb.core.archive import LocalBackend, RawArchive
from ptb.core.warehouse import db, load


@pytest.fixture
def con(tmp_path):
    connection = db.connect(tmp_path / "test.duckdb")
    yield connection
    connection.close()


def _tables(con):
    return {row[0] for row in con.execute("SHOW TABLES").fetchall()}


def test_connect_creates_the_expected_tables(con):
    tables = _tables(con)
    assert {"dim_competition", "src_footballdata_match", "meta_archive_loaded"} <= tables


def test_apply_schema_is_idempotent(con):
    before = _tables(con)
    db.apply_schema(con)
    db.apply_schema(con)
    assert _tables(con) == before


def test_competitions_are_seeded(con):
    count = con.execute("SELECT count(*) FROM dim_competition WHERE competition_id = 'E0'").fetchone()[0]
    assert count == 1


def test_mark_loaded_then_is_loaded(con):
    key = "footballdata/season/E0__2024-25__20260802T101500Z.json.gz"
    assert not load.is_loaded(con, key)
    load.mark_loaded(con, key)
    assert load.is_loaded(con, key)


def test_mark_loaded_twice_keeps_one_row(con):
    key = "footballdata/season/E0__2024-25__20260802T101500Z.json.gz"
    load.mark_loaded(con, key)
    load.mark_loaded(con, key)
    count = con.execute("SELECT count(*) FROM meta_archive_loaded").fetchone()[0]
    assert count == 1


def test_rebuild_over_an_empty_archive_reports_nothing(con, tmp_path):
    archive = RawArchive(LocalBackend(tmp_path / "raw"))
    assert load.rebuild(con, archive) == {}


def test_rebuild_ignores_a_source_with_no_loader(con, tmp_path):
    archive = RawArchive(LocalBackend(tmp_path / "raw"))
    archive.write("mystery", "thing", {"n": 1},
                  captured_at=dt.datetime(2026, 8, 2, 10, 15, 0))
    assert load.rebuild(con, archive) == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_warehouse_db.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ptb.core.warehouse'`

- [ ] **Step 3: Write schema.sql**

`packages/ptb-core/src/ptb/core/warehouse/schema.sql`:

```sql
-- Bookkeeping: which archive keys have been folded into this warehouse.
-- Makes every loader idempotent and lets `rebuild` skip finished work.
CREATE TABLE IF NOT EXISTS meta_archive_loaded (
    archive_key TEXT PRIMARY KEY,
    source      TEXT NOT NULL,
    loaded_at   TIMESTAMP NOT NULL
);

-- ---------------------------------------------------------------- dimensions

CREATE TABLE IF NOT EXISTS dim_competition (
    competition_id TEXT PRIMARY KEY,
    country        TEXT NOT NULL,
    name           TEXT NOT NULL,
    tier           INTEGER,
    gender         TEXT NOT NULL DEFAULT 'M'
);

INSERT OR REPLACE INTO dim_competition (competition_id, country, name, tier, gender) VALUES
    ('E0',  'England', 'Premier League',   1, 'M'),
    ('E1',  'England', 'Championship',     2, 'M'),
    ('SP1', 'Spain',   'La Liga',          1, 'M'),
    ('SP2', 'Spain',   'Segunda Division', 2, 'M'),
    ('I1',  'Italy',   'Serie A',          1, 'M'),
    ('I2',  'Italy',   'Serie B',          2, 'M'),
    ('D1',  'Germany', 'Bundesliga',       1, 'M'),
    ('D2',  'Germany', '2. Bundesliga',    2, 'M'),
    ('F1',  'France',  'Ligue 1',          1, 'M'),
    ('F2',  'France',  'Ligue 2',          2, 'M');

-- ------------------------------------------------------------- source-faithful
-- One table per source per entity. Never merged, never reconciled: where two
-- sources disagree, both values survive here and a conformed view picks one.

CREATE TABLE IF NOT EXISTS src_footballdata_match (
    competition   TEXT NOT NULL,
    season        TEXT NOT NULL,
    match_date    DATE NOT NULL,
    kickoff_time  TIME,
    home_team     TEXT NOT NULL,
    away_team     TEXT NOT NULL,
    home_goals    INTEGER,
    away_goals    INTEGER,
    result        TEXT,
    ht_home_goals INTEGER,
    ht_away_goals INTEGER,
    ht_result     TEXT,
    home_shots    INTEGER,
    away_shots    INTEGER,
    home_sot      INTEGER,
    away_sot      INTEGER,
    home_fouls    INTEGER,
    away_fouls    INTEGER,
    home_corners  INTEGER,
    away_corners  INTEGER,
    home_yellows  INTEGER,
    away_yellows  INTEGER,
    home_reds     INTEGER,
    away_reds     INTEGER,
    referee       TEXT,
    odds_home     DOUBLE,
    odds_draw     DOUBLE,
    odds_away     DOUBLE,
    archive_key   TEXT NOT NULL,
    PRIMARY KEY (competition, season, match_date, home_team, away_team)
);
```

Column names are spelled out rather than kept as football-data's codes because
`AS` (away shots) is a reserved SQL word and would need quoting everywhere.

- [ ] **Step 4: Write db.py**

`packages/ptb-core/src/ptb/core/warehouse/db.py`:

```python
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
```

- [ ] **Step 5: Write load.py**

`packages/ptb-core/src/ptb/core/warehouse/load.py`:

```python
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
```

`packages/ptb-core/src/ptb/core/warehouse/__init__.py`:

```python
from . import db, load

__all__ = ["db", "load"]
```

- [ ] **Step 6: Add the `rebuild` command**

In `cli.py`'s `build_parser`, before `return parser`:

```python
    rebuild = subparsers.add_parser("rebuild", help="replay the archive into the warehouse")
    rebuild.add_argument("--source", help="comma-separated source names")
```

and add the handler, extending `_HANDLERS`:

```python
def _cmd_rebuild(args) -> int:
    from .archive import RawArchive
    from .warehouse import db, load

    sources = [s.strip() for s in args.source.split(",")] if args.source else None
    con = db.connect()
    try:
        written = load.rebuild(con, RawArchive(), sources=sources)
    finally:
        con.close()

    if not written:
        print("nothing to load")
        return 0
    for source, rows in sorted(written.items()):
        print("{}: {} rows".format(source, rows))
    return 0


_HANDLERS = {"ingest": _cmd_ingest, "rebuild": _cmd_rebuild}
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/test_warehouse_db.py -v`
Expected: 7 passed

- [ ] **Step 8: Commit**

```bash
git add packages/ptb-core/src/ptb/core/warehouse/ packages/ptb-core/src/ptb/core/cli.py tests/test_warehouse_db.py
git commit -m "Add the DuckDB warehouse skeleton and ptb rebuild"
```

---

### Task 6: football-data loader

**Files:**
- Create: `packages/ptb-core/src/ptb/core/warehouse/loaders/__init__.py`, `footballdata.py`
- Modify: `packages/ptb-core/src/ptb/core/warehouse/__init__.py` (import loaders for registration)
- Create: `tests/fixtures/footballdata_e0_2024_sample.json` (golden payload)
- Test: `tests/test_footballdata_loader.py`

**Interfaces:**
- Consumes: `LOADERS` dict from `warehouse.load`
- Produces: `load_footballdata(con, payload: dict, archive_key: str) -> int`, `parse_date(text: str) -> date | None`, `parse_row(row: dict) -> dict | None`

- [ ] **Step 1: Create the golden payload fixture**

Real data, trimmed to five rows, so the loader is testable offline and the
payload shape stays documented even if the site changes.

`tests/fixtures/footballdata_e0_2024_sample.json`:

```json
{
  "source": "footballdata",
  "competition": "E0",
  "season": "2024/25",
  "url": "https://www.football-data.co.uk/mmz4281/2425/E0.csv",
  "fetched_at": "2026-08-02T10:15:00Z",
  "csv": "Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HTHG,HTAG,HTR,Referee,HS,AS,HST,AST,HF,AF,HC,AC,HY,AY,HR,AR,B365H,B365D,B365A\nE0,16/08/2024,20:00,Man United,Fulham,1,0,H,0,0,D,R Jones,14,11,3,4,11,10,7,4,2,1,0,0,1.63,4.00,5.25\nE0,17/08/2024,12:30,Ipswich,Liverpool,0,2,A,0,0,D,T Robinson,7,15,2,6,10,8,2,7,1,1,0,0,7.50,4.75,1.40\nE0,17/08/2024,15:00,Arsenal,Wolves,2,0,H,1,0,H,S Hooper,20,6,7,1,8,12,10,2,1,3,0,0,1.25,6.50,11.00\nE0,17/08/2024,15:00,Everton,Brighton,0,3,A,0,1,A,S Barrott,10,14,3,7,9,7,5,6,2,0,0,0,3.10,3.50,2.30\nE0,17/08/2024,17:30,Newcastle,Southampton,1,0,H,0,0,D,C Pawson,17,7,5,2,7,11,9,3,1,2,0,0,1.36,5.25,8.50\n"
}
```

- [ ] **Step 2: Write the failing test**

`tests/test_footballdata_loader.py`:

```python
import datetime as dt
import json
from pathlib import Path

import pytest

from ptb.core.warehouse import db
from ptb.core.warehouse.loaders import footballdata as loader

FIXTURE = Path(__file__).parent / "fixtures" / "footballdata_e0_2024_sample.json"


@pytest.fixture
def con(tmp_path):
    connection = db.connect(tmp_path / "test.duckdb")
    yield connection
    connection.close()


@pytest.fixture
def payload():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_parse_date_handles_both_era_formats():
    assert loader.parse_date("16/08/2024") == dt.date(2024, 8, 16)
    assert loader.parse_date("14/08/93") == dt.date(1993, 8, 14)
    assert loader.parse_date("19/08/00") == dt.date(2000, 8, 19)


def test_parse_date_returns_none_for_junk():
    assert loader.parse_date("") is None
    assert loader.parse_date("not a date") is None


def test_load_writes_every_row(con, payload):
    rows = loader.load_footballdata(con, payload, "some/key.json.gz")
    assert rows == 5
    count = con.execute("SELECT count(*) FROM src_footballdata_match").fetchone()[0]
    assert count == 5


def test_load_maps_columns_correctly(con, payload):
    loader.load_footballdata(con, payload, "some/key.json.gz")
    row = con.execute(
        "SELECT season, kickoff_time, home_goals, away_goals, result, "
        "       home_shots, away_shots, home_corners, referee, odds_home "
        "FROM src_footballdata_match WHERE home_team = 'Arsenal'"
    ).fetchone()

    assert row[0] == "2024/25"
    assert row[1] == dt.time(15, 0)
    assert (row[2], row[3], row[4]) == (2, 0, "H")
    assert (row[5], row[6]) == (20, 6)
    assert row[7] == 10
    assert row[8] == "S Hooper"
    assert row[9] == pytest.approx(1.25)


def test_loading_twice_changes_nothing(con, payload):
    loader.load_footballdata(con, payload, "some/key.json.gz")
    first = con.execute(
        "SELECT * FROM src_footballdata_match ORDER BY match_date, home_team"
    ).fetchall()

    loader.load_footballdata(con, payload, "some/key.json.gz")
    second = con.execute(
        "SELECT * FROM src_footballdata_match ORDER BY match_date, home_team"
    ).fetchall()

    assert first == second
    assert con.execute("SELECT count(*) FROM src_footballdata_match").fetchone()[0] == 5


def test_load_tolerates_a_1993_file_with_no_match_stats(con):
    """1993/94 has 28 columns, no Time, no shots, and trailing empty headers."""
    payload = {
        "source": "footballdata",
        "competition": "E0",
        "season": "1993/94",
        "url": "https://www.football-data.co.uk/mmz4281/9394/E0.csv",
        "fetched_at": "2026-08-02T10:15:00Z",
        "csv": (
            "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,,,,\n"
            "E0,14/08/93,Arsenal,Coventry,0,3,A,,,,\n"
            "E0,14/08/93,Liverpool,Sheffield Weds,2,0,H,,,,\n"
        ),
    }
    rows = loader.load_footballdata(con, payload, "old/key.json.gz")
    assert rows == 2

    row = con.execute(
        "SELECT kickoff_time, home_shots, home_goals, away_goals "
        "FROM src_footballdata_match WHERE home_team = 'Arsenal'"
    ).fetchone()
    assert row == (None, None, 0, 3)


def test_load_skips_blank_trailing_rows(con):
    payload = {
        "source": "footballdata", "competition": "E0", "season": "2024/25",
        "url": "u", "fetched_at": "2026-08-02T10:15:00Z",
        "csv": (
            "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR\n"
            "E0,16/08/2024,Man United,Fulham,1,0,H\n"
            ",,,,,,\n"
            "\n"
        ),
    }
    assert loader.load_footballdata(con, payload, "k") == 1


def test_loader_is_registered():
    from ptb.core.warehouse.load import LOADERS
    assert "footballdata" in LOADERS


def test_rebuild_is_deterministic(con, tmp_path, payload):
    """The property the whole design rests on: replaying the archive twice
    into a fresh warehouse gives byte-identical contents."""
    from ptb.core.archive import LocalBackend, RawArchive
    from ptb.core.warehouse import load

    archive = RawArchive(LocalBackend(tmp_path / "raw"))
    archive.write("footballdata", "season", payload, label="E0__2024-25",
                  captured_at=dt.datetime(2026, 8, 2, 10, 15, 0))

    load.rebuild(con, archive)
    first = con.execute(
        "SELECT * FROM src_footballdata_match ORDER BY match_date, home_team"
    ).fetchall()

    second_con = db.connect(tmp_path / "second.duckdb")
    load.rebuild(second_con, archive)
    second = second_con.execute(
        "SELECT * FROM src_footballdata_match ORDER BY match_date, home_team"
    ).fetchall()
    second_con.close()

    assert first == second
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_footballdata_loader.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ptb.core.warehouse.loaders'`

- [ ] **Step 4: Write the loader**

`packages/ptb-core/src/ptb/core/warehouse/loaders/footballdata.py`:

```python
"""football-data.co.uk CSV -> src_footballdata_match.

Reads strictly by column name. Column availability varies by era -- 1993/94
has no match statistics and no kickoff time, and its header carries trailing
empty names -- so every optional column is fetched defensively and absence is
normal, not an error.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import logging
from typing import Any, Dict, Optional

import duckdb

from ..load import LOADERS

log = logging.getLogger(__name__)

_DATE_FORMATS = ("%d/%m/%Y", "%d/%m/%y")

# CSV column -> warehouse column
_STATS = {
    "FTHG": "home_goals", "FTAG": "away_goals", "FTR": "result",
    "HTHG": "ht_home_goals", "HTAG": "ht_away_goals", "HTR": "ht_result",
    "HS": "home_shots", "AS": "away_shots",
    "HST": "home_sot", "AST": "away_sot",
    "HF": "home_fouls", "AF": "away_fouls",
    "HC": "home_corners", "AC": "away_corners",
    "HY": "home_yellows", "AY": "away_yellows",
    "HR": "home_reds", "AR": "away_reds",
}
_INT_COLUMNS = {v for k, v in _STATS.items() if k not in ("FTR", "HTR")}
_ODDS = {"B365H": "odds_home", "B365D": "odds_draw", "B365A": "odds_away"}

_COLUMNS = [
    "competition", "season", "match_date", "kickoff_time", "home_team", "away_team",
    "home_goals", "away_goals", "result", "ht_home_goals", "ht_away_goals", "ht_result",
    "home_shots", "away_shots", "home_sot", "away_sot", "home_fouls", "away_fouls",
    "home_corners", "away_corners", "home_yellows", "away_yellows",
    "home_reds", "away_reds", "referee", "odds_home", "odds_draw", "odds_away",
    "archive_key",
]


def parse_date(text: Optional[str]) -> Optional[dt.date]:
    """football-data uses DD/MM/YYYY in modern files and DD/MM/YY in old ones."""
    if not text:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return dt.datetime.strptime(text.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _parse_time(text: Optional[str]) -> Optional[dt.time]:
    if not text:
        return None
    try:
        return dt.datetime.strptime(text.strip(), "%H:%M").time()
    except ValueError:
        return None


def _get(row: Dict[str, Any], column: str) -> Optional[str]:
    value = row.get(column)
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _as_int(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _as_float(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_row(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """One CSV row -> one warehouse row, or None if it is not a real match."""
    home = _get(row, "HomeTeam")
    away = _get(row, "AwayTeam")
    match_date = parse_date(_get(row, "Date"))
    if not home or not away or match_date is None:
        return None

    parsed: Dict[str, Any] = {
        "match_date": match_date,
        "kickoff_time": _parse_time(_get(row, "Time")),
        "home_team": home,
        "away_team": away,
        "referee": _get(row, "Referee"),
    }
    for source_column, target in _STATS.items():
        raw = _get(row, source_column)
        parsed[target] = _as_int(raw) if target in _INT_COLUMNS else raw
    for source_column, target in _ODDS.items():
        parsed[target] = _as_float(_get(row, source_column))
    return parsed


def load_footballdata(
    con: duckdb.DuckDBPyConnection,
    payload: dict,
    archive_key: str,
) -> int:
    competition = payload["competition"]
    season = payload["season"]

    reader = csv.DictReader(io.StringIO(payload["csv"]))
    rows = []
    for raw_row in reader:
        # 1993/94 headers end in empty names, which DictReader maps to None.
        raw_row.pop(None, None)
        raw_row.pop("", None)
        parsed = parse_row(raw_row)
        if parsed is None:
            continue
        parsed["competition"] = competition
        parsed["season"] = season
        parsed["archive_key"] = archive_key
        rows.append([parsed.get(column) for column in _COLUMNS])

    if rows:
        con.executemany(
            "INSERT OR REPLACE INTO src_footballdata_match ({}) VALUES ({})".format(
                ", ".join(_COLUMNS), ", ".join("?" * len(_COLUMNS))
            ),
            rows,
        )
    log.debug("loaded %d rows from %s %s", len(rows), competition, season)
    return len(rows)


LOADERS["footballdata"] = load_footballdata
```

`packages/ptb-core/src/ptb/core/warehouse/loaders/__init__.py`:

```python
from . import footballdata  # noqa: F401  -- imported for registration side effect
```

Append to `packages/ptb-core/src/ptb/core/warehouse/__init__.py`:

```python
from . import loaders  # noqa: F401,E402  -- registers loaders into LOADERS
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_footballdata_loader.py -v`
Expected: 9 passed

- [ ] **Step 6: Verify end to end against real data**

```bash
uv run ptb ingest footballdata --competition E0,SP1 --season 2024/25,1993/94
uv run ptb rebuild
```

Expected: `footballdata: <n> rows` with n around 1,100. Then confirm the era
handling actually worked on real files:

```bash
uv run python -c "
from ptb.core.warehouse import db
con = db.connect()
print(con.execute('''
  SELECT season, competition, count(*) AS matches,
         count(home_shots) AS with_shots, count(kickoff_time) AS with_time
  FROM src_footballdata_match GROUP BY 1, 2 ORDER BY 1, 2
''').fetchall())
"
```

Expected: 1993/94 rows present with `with_shots = 0` and `with_time = 0`;
2024/25 rows with both populated.

- [ ] **Step 7: Commit**

```bash
git add packages/ptb-core/src/ptb/core/warehouse/ tests/
git commit -m "Load football-data seasons into the warehouse"
```

---

### Task 7: Team normalization and `dim_team`

**Files:**
- Create: `packages/ptb-core/src/ptb/core/identity/__init__.py`, `text.py`, `teams.py`, `team_aliases.yaml`
- Modify: `packages/ptb-core/src/ptb/core/warehouse/schema.sql` (add team tables)
- Test: `tests/test_identity_teams.py`

**Interfaces:**
- Consumes: warehouse connection
- Produces:
  - `normalize_name(text: str) -> str`
  - `load_aliases(path: Path | None = None) -> dict[str, str]`
  - `canonical_name(raw: str, aliases: dict[str, str] | None = None) -> str`
  - `resolve_team(con, raw_name: str, source: str, country: str | None = None) -> int` (returns `team_id`)

- [ ] **Step 1: Write the failing test**

`tests/test_identity_teams.py`:

```python
import pytest

from ptb.core.identity import teams, text
from ptb.core.warehouse import db


@pytest.fixture
def con(tmp_path):
    connection = db.connect(tmp_path / "test.duckdb")
    yield connection
    connection.close()


def test_normalize_lowercases_and_collapses_space():
    assert text.normalize_name("  Manchester   United  ") == "manchester united"


def test_normalize_strips_diacritics():
    assert text.normalize_name("Atlético Madrid") == "atletico madrid"
    assert text.normalize_name("Beşiktaş") == "besiktas"


def test_normalize_strips_punctuation():
    """M'gladbach and Nott'm Forest both appear in football-data files."""
    assert text.normalize_name("M'gladbach") == "mgladbach"
    assert text.normalize_name("Nott'm Forest") == "nottm forest"
    assert text.normalize_name("St. Pauli") == "st pauli"


def test_normalize_is_idempotent():
    once = text.normalize_name("Atlético Madrid")
    assert text.normalize_name(once) == once


def test_aliases_map_variants_to_one_canonical_name():
    aliases = teams.load_aliases()
    assert teams.canonical_name("Man United", aliases) == "Manchester United"
    assert teams.canonical_name("Man Utd", aliases) == "Manchester United"
    assert teams.canonical_name("Manchester United", aliases) == "Manchester United"


def test_canonical_name_passes_through_unknown_clubs():
    assert teams.canonical_name("Fulham", {}) == "Fulham"


def test_resolve_team_creates_then_reuses_one_id(con):
    first = teams.resolve_team(con, "Man United", source="footballdata")
    second = teams.resolve_team(con, "Man United", source="footballdata")
    assert first == second
    assert con.execute("SELECT count(*) FROM dim_team").fetchone()[0] == 1


def test_variants_across_sources_resolve_to_one_team(con):
    a = teams.resolve_team(con, "Man United", source="footballdata")
    b = teams.resolve_team(con, "Man Utd", source="fotmob")
    assert a == b
    assert con.execute("SELECT count(*) FROM dim_team").fetchone()[0] == 1
    assert con.execute("SELECT count(*) FROM map_team_source").fetchone()[0] == 2


def test_map_records_each_source_spelling(con):
    teams.resolve_team(con, "Man United", source="footballdata")
    rows = con.execute(
        "SELECT source, source_team_name FROM map_team_source"
    ).fetchall()
    assert rows == [("footballdata", "Man United")]


def test_distinct_clubs_get_distinct_ids(con):
    a = teams.resolve_team(con, "Man United", source="footballdata")
    b = teams.resolve_team(con, "Man City", source="footballdata")
    assert a != b
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_identity_teams.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ptb.core.identity'`

- [ ] **Step 3: Extend the schema**

Append to `packages/ptb-core/src/ptb/core/warehouse/schema.sql`:

```sql
-- ----------------------------------------------------------------- identity

CREATE SEQUENCE IF NOT EXISTS seq_team_id START 1;

CREATE TABLE IF NOT EXISTS dim_team (
    team_id         BIGINT PRIMARY KEY,
    canonical_name  TEXT NOT NULL,
    normalized_name TEXT NOT NULL UNIQUE,
    country         TEXT,
    gender          TEXT NOT NULL DEFAULT 'M'
);

CREATE TABLE IF NOT EXISTS map_team_source (
    team_id          BIGINT NOT NULL,
    source           TEXT NOT NULL,
    source_team_name TEXT NOT NULL,
    PRIMARY KEY (source, source_team_name)
);
```

- [ ] **Step 4: Write text.py**

`packages/ptb-core/src/ptb/core/identity/text.py`:

```python
"""Name normalization, shared by team and (later) player resolution.

Sources spell the same club a dozen ways -- 'Man United', 'Man Utd',
'Manchester Utd' -- and differ on diacritics. Normalizing to a comparable
key is the first half of resolving them; the alias table is the second.
"""
from __future__ import annotations

import re
import unicodedata

_PUNCTUATION = re.compile(r"[^\w\s]", flags=re.UNICODE)
_WHITESPACE = re.compile(r"\s+")


def strip_diacritics(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize_name(value: str) -> str:
    """Lowercase, de-accent, drop punctuation, collapse whitespace."""
    folded = strip_diacritics(value).lower()
    folded = _PUNCTUATION.sub("", folded)
    return _WHITESPACE.sub(" ", folded).strip()
```

- [ ] **Step 5: Write the alias file**

`packages/ptb-core/src/ptb/core/identity/team_aliases.yaml`:

```yaml
# Hand-maintained. Keys are normalized variants (see identity/text.py);
# values are the canonical club name. Small and stable by design -- roughly
# one entry per club that a source spells unusually.
"man united": Manchester United
"man utd": Manchester United
"manchester utd": Manchester United
"man city": Manchester City
"manchester city": Manchester City
"nottm forest": Nottingham Forest
"sheffield weds": Sheffield Wednesday
"sheffield united": Sheffield United
"wolves": Wolverhampton Wanderers
"west brom": West Bromwich Albion
"west ham": West Ham United
"newcastle": Newcastle United
"tottenham": Tottenham Hotspur
"spurs": Tottenham Hotspur
"leicester": Leicester City
"norwich": Norwich City
"ath bilbao": Athletic Bilbao
"ath madrid": Atletico Madrid
"atletico madrid": Atletico Madrid
"espanol": Espanyol
"sociedad": Real Sociedad
"betis": Real Betis
"celta": Celta Vigo
"mgladbach": Borussia Monchengladbach
"monchengladbach": Borussia Monchengladbach
"dortmund": Borussia Dortmund
"bayern munich": Bayern Munich
"ein frankfurt": Eintracht Frankfurt
"leverkusen": Bayer Leverkusen
"inter": Inter Milan
"ac milan": AC Milan
"milan": AC Milan
"juventus": Juventus
"paris sg": Paris Saint-Germain
"psg": Paris Saint-Germain
"marseille": Olympique Marseille
"lyon": Olympique Lyonnais
```

- [ ] **Step 6: Write teams.py**

`packages/ptb-core/src/ptb/core/identity/teams.py`:

```python
"""Resolve a source's spelling of a club to one `dim_team` row."""
from __future__ import annotations

import functools
from pathlib import Path
from typing import Dict, Optional

import duckdb
import yaml

from .text import normalize_name

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
```

`packages/ptb-core/src/ptb/core/identity/__init__.py`:

```python
from . import teams, text

__all__ = ["teams", "text"]
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/test_identity_teams.py -v`
Expected: 10 passed

- [ ] **Step 8: Commit**

```bash
git add packages/ptb-core/src/ptb/core/identity/ packages/ptb-core/src/ptb/core/warehouse/schema.sql tests/test_identity_teams.py
git commit -m "Resolve club name variants to one dim_team"
```

---

### Task 8: Match resolution with the ±36h window

**Files:**
- Create: `packages/ptb-core/src/ptb/core/identity/matches.py`
- Modify: `packages/ptb-core/src/ptb/core/warehouse/schema.sql` (match tables)
- Modify: `packages/ptb-core/src/ptb/core/warehouse/load.py` (call resolution after loaders)
- Modify: `packages/ptb-core/src/ptb/core/cli.py` (add `coverage`)
- Test: `tests/test_identity_matches.py`

**Interfaces:**
- Consumes: `resolve_team`, warehouse connection
- Produces:
  - `RESOLUTION_WINDOW = timedelta(hours=36)`
  - `match_key(competition, season, home_norm, away_norm, kickoff) -> str` (debugging column)
  - `resolve_match(con, *, source, source_match_id, competition, season, kickoff, home_team, away_team) -> int | None`
  - `resolve_footballdata(con) -> int` — resolves every unresolved `src_footballdata_match` row
  - `MULTIPLE_CANDIDATES`, `UNPARSEABLE` reason constants

- [ ] **Step 1: Write the failing test**

`tests/test_identity_matches.py`:

```python
import datetime as dt

import pytest

from ptb.core.identity import matches
from ptb.core.warehouse import db


@pytest.fixture
def con(tmp_path):
    connection = db.connect(tmp_path / "test.duckdb")
    yield connection
    connection.close()


def _resolve(con, source, kickoff, source_match_id="m1",
             home="Portland Thorns", away="OL Reign"):
    return matches.resolve_match(
        con, source=source, source_match_id=source_match_id,
        competition="NWSL", season="2025", kickoff=kickoff,
        home_team=home, away_team=away,
    )


def test_resolve_creates_a_match(con):
    match_id = _resolve(con, "fotmob", dt.datetime(2025, 6, 15, 2, 30))
    assert match_id is not None
    assert con.execute("SELECT count(*) FROM dim_match").fetchone()[0] == 1


def test_a_late_nwsl_kickoff_resolves_across_the_utc_date_boundary(con):
    """A 19:30 Pacific Saturday kickoff is 02:30 UTC Sunday. A source
    reporting local date and one reporting UTC must still land on one match."""
    utc = _resolve(con, "fotmob", dt.datetime(2025, 6, 15, 2, 30), "fm-1")
    local = _resolve(con, "asa", dt.datetime(2025, 6, 14, 19, 30), "asa-1")

    assert utc == local
    assert con.execute("SELECT count(*) FROM dim_match").fetchone()[0] == 1
    assert con.execute("SELECT count(*) FROM map_match_source").fetchone()[0] == 2


def test_the_same_source_match_id_is_stable(con):
    first = _resolve(con, "fotmob", dt.datetime(2025, 6, 15, 2, 30), "fm-1")
    second = _resolve(con, "fotmob", dt.datetime(2025, 6, 15, 2, 30), "fm-1")
    assert first == second
    assert con.execute("SELECT count(*) FROM dim_match").fetchone()[0] == 1


def test_fixtures_outside_the_window_are_separate_matches(con):
    first = _resolve(con, "fotmob", dt.datetime(2025, 6, 15, 2, 30), "fm-1")
    second = _resolve(con, "fotmob", dt.datetime(2025, 8, 20, 2, 30), "fm-2")
    assert first != second
    assert con.execute("SELECT count(*) FROM dim_match").fetchone()[0] == 2


def test_reversed_fixture_is_a_different_match(con):
    """Home and away are not interchangeable: the return leg is its own match."""
    home_leg = _resolve(con, "fotmob", dt.datetime(2025, 6, 15, 2, 30), "fm-1")
    away_leg = _resolve(con, "fotmob", dt.datetime(2025, 6, 15, 2, 30), "fm-2",
                        home="OL Reign", away="Portland Thorns")
    assert home_leg != away_leg


def test_ambiguity_is_recorded_rather_than_guessed(con):
    """Two candidates inside the window should not happen, but if it does,
    resolution must fail loudly instead of picking one."""
    _resolve(con, "fotmob", dt.datetime(2025, 6, 15, 2, 30), "fm-1")
    _resolve(con, "fotmob", dt.datetime(2025, 6, 16, 2, 30), "fm-2")

    result = _resolve(con, "asa", dt.datetime(2025, 6, 15, 14, 0), "asa-1")

    assert result is None
    row = con.execute(
        "SELECT source, source_match_id, reason FROM unresolved_match"
    ).fetchone()
    assert row[0] == "asa"
    assert row[1] == "asa-1"
    assert row[2] == matches.MULTIPLE_CANDIDATES


def test_ambiguous_rows_are_not_dropped_from_the_source_table(con):
    """Unresolved means unmapped, never deleted."""
    _resolve(con, "fotmob", dt.datetime(2025, 6, 15, 2, 30), "fm-1")
    _resolve(con, "fotmob", dt.datetime(2025, 6, 16, 2, 30), "fm-2")
    _resolve(con, "asa", dt.datetime(2025, 6, 15, 14, 0), "asa-1")

    assert con.execute("SELECT count(*) FROM unresolved_match").fetchone()[0] == 1
    assert con.execute("SELECT count(*) FROM dim_match").fetchone()[0] == 2


def test_match_key_is_stored_for_debugging(con):
    _resolve(con, "fotmob", dt.datetime(2025, 6, 15, 2, 30))
    key = con.execute("SELECT match_key FROM dim_match").fetchone()[0]
    assert "2025-06-15" in key
    assert "portland thorns" in key


def test_resolve_footballdata_maps_loaded_rows(con):
    con.execute(
        "INSERT INTO src_footballdata_match "
        "(competition, season, match_date, kickoff_time, home_team, away_team, archive_key) "
        "VALUES ('E0', '2024/25', DATE '2024-08-16', TIME '20:00', "
        "        'Man United', 'Fulham', 'k')"
    )
    resolved = matches.resolve_footballdata(con)

    assert resolved == 1
    row = con.execute(
        "SELECT m.competition, m.season, t.canonical_name "
        "FROM dim_match m JOIN dim_team t ON t.team_id = m.home_team_id"
    ).fetchone()
    assert row == ("E0", "2024/25", "Manchester United")


def test_resolve_footballdata_is_idempotent(con):
    con.execute(
        "INSERT INTO src_footballdata_match "
        "(competition, season, match_date, kickoff_time, home_team, away_team, archive_key) "
        "VALUES ('E0', '2024/25', DATE '2024-08-16', TIME '20:00', "
        "        'Man United', 'Fulham', 'k')"
    )
    matches.resolve_footballdata(con)
    matches.resolve_footballdata(con)
    assert con.execute("SELECT count(*) FROM dim_match").fetchone()[0] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_identity_matches.py -v`
Expected: FAIL — `ImportError: cannot import name 'matches'`

- [ ] **Step 3: Extend the schema**

Append to `packages/ptb-core/src/ptb/core/warehouse/schema.sql`:

```sql
CREATE SEQUENCE IF NOT EXISTS seq_match_id START 1;

CREATE TABLE IF NOT EXISTS dim_match (
    match_id     BIGINT PRIMARY KEY,
    competition  TEXT NOT NULL,
    season       TEXT NOT NULL,
    kickoff_utc  TIMESTAMP NOT NULL,
    home_team_id BIGINT NOT NULL,
    away_team_id BIGINT NOT NULL,
    match_key    TEXT NOT NULL  -- human-readable, for debugging; never a join key
);

CREATE TABLE IF NOT EXISTS map_match_source (
    match_id        BIGINT NOT NULL,
    source          TEXT NOT NULL,
    source_match_id TEXT NOT NULL,
    method          TEXT NOT NULL,
    confidence      DOUBLE NOT NULL,
    PRIMARY KEY (source, source_match_id)
);

-- Resolution failures land here rather than being dropped. `ptb verify`
-- reports them; the underlying src_ rows are untouched.
CREATE TABLE IF NOT EXISTS unresolved_match (
    source          TEXT NOT NULL,
    source_match_id TEXT NOT NULL,
    reason          TEXT NOT NULL,
    detail          TEXT,
    PRIMARY KEY (source, source_match_id)
);
```

- [ ] **Step 4: Write matches.py**

`packages/ptb-core/src/ptb/core/identity/matches.py`:

```python
"""Resolve a source's match to one `dim_match` row.

Matching on exact date breaks on late kickoffs: a 19:30 Pacific Saturday NWSL
game is 02:30 UTC on Sunday, so a source reporting local date and one
reporting UTC would produce two matches for one fixture -- and every
cross-source join would silently return nothing. Resolution therefore uses a
+/-36 hour window around kickoff rather than date equality.

Where more than one candidate falls inside the window, resolution fails and
records the reason instead of guessing.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Optional

import duckdb

from .teams import resolve_team
from .text import normalize_name

log = logging.getLogger(__name__)

RESOLUTION_WINDOW = dt.timedelta(hours=36)

MULTIPLE_CANDIDATES = "multiple_candidates"
UNPARSEABLE = "unparseable"


def match_key(
    competition: str,
    season: str,
    home_normalized: str,
    away_normalized: str,
    kickoff: dt.datetime,
) -> str:
    """Human-readable identity string. Debugging aid only -- never join on it."""
    return "{}|{}|{}|{}-{}".format(
        competition, season, kickoff.date().isoformat(),
        home_normalized, away_normalized,
    )


def _record_unresolved(
    con: duckdb.DuckDBPyConnection,
    source: str,
    source_match_id: str,
    reason: str,
    detail: Optional[str] = None,
) -> None:
    con.execute(
        "INSERT OR REPLACE INTO unresolved_match "
        "(source, source_match_id, reason, detail) VALUES (?, ?, ?, ?)",
        [source, source_match_id, reason, detail],
    )


def resolve_match(
    con: duckdb.DuckDBPyConnection,
    *,
    source: str,
    source_match_id: str,
    competition: str,
    season: str,
    kickoff: dt.datetime,
    home_team: str,
    away_team: str,
) -> Optional[int]:
    """Find or create the match. Returns its id, or None if ambiguous."""
    existing = con.execute(
        "SELECT match_id FROM map_match_source WHERE source = ? AND source_match_id = ?",
        [source, source_match_id],
    ).fetchone()
    if existing is not None:
        return existing[0]

    home_id = resolve_team(con, home_team, source=source)
    away_id = resolve_team(con, away_team, source=source)

    candidates = con.execute(
        "SELECT match_id FROM dim_match "
        "WHERE competition = ? AND season = ? "
        "  AND home_team_id = ? AND away_team_id = ? "
        "  AND abs(epoch(kickoff_utc) - epoch(?::TIMESTAMP)) <= ?",
        [competition, season, home_id, away_id, kickoff,
         RESOLUTION_WINDOW.total_seconds()],
    ).fetchall()

    if len(candidates) > 1:
        log.warning(
            "ambiguous match: %s/%s has %d candidates within %s",
            source, source_match_id, len(candidates), RESOLUTION_WINDOW,
        )
        _record_unresolved(
            con, source, source_match_id, MULTIPLE_CANDIDATES,
            "{} candidates within {}".format(len(candidates), RESOLUTION_WINDOW),
        )
        return None

    if candidates:
        match_id = candidates[0][0]
        method = "window"
        confidence = 0.9
    else:
        match_id = con.execute("SELECT nextval('seq_match_id')").fetchone()[0]
        con.execute(
            "INSERT INTO dim_match (match_id, competition, season, kickoff_utc, "
            "home_team_id, away_team_id, match_key) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [match_id, competition, season, kickoff, home_id, away_id,
             match_key(competition, season, normalize_name(home_team),
                       normalize_name(away_team), kickoff)],
        )
        method = "created"
        confidence = 1.0

    con.execute(
        "INSERT OR REPLACE INTO map_match_source "
        "(match_id, source, source_match_id, method, confidence) VALUES (?, ?, ?, ?, ?)",
        [match_id, source, source_match_id, method, confidence],
    )
    con.execute(
        "DELETE FROM unresolved_match WHERE source = ? AND source_match_id = ?",
        [source, source_match_id],
    )
    return match_id


def resolve_footballdata(con: duckdb.DuckDBPyConnection) -> int:
    """Resolve every football-data row into dim_match. Idempotent."""
    rows = con.execute(
        "SELECT competition, season, match_date, kickoff_time, home_team, away_team "
        "FROM src_footballdata_match ORDER BY match_date, home_team"
    ).fetchall()

    resolved = 0
    for competition, season, match_date, kickoff_time, home, away in rows:
        kickoff = dt.datetime.combine(match_date, kickoff_time or dt.time(15, 0))
        source_match_id = "{}|{}|{}|{}|{}".format(
            competition, season, match_date.isoformat(), home, away
        )
        if resolve_match(
            con, source="footballdata", source_match_id=source_match_id,
            competition=competition, season=season, kickoff=kickoff,
            home_team=home, away_team=away,
        ) is not None:
            resolved += 1
    return resolved
```

Note the `or dt.time(15, 0)` default: pre-2000 files carry no kickoff time, and
a 36-hour window makes a nominal 15:00 harmless.

Append to `packages/ptb-core/src/ptb/core/identity/__init__.py`:

```python
from . import matches  # noqa: F401,E402

__all__ = ["teams", "text", "matches"]
```

- [ ] **Step 5: Wire resolution into `rebuild`**

In `packages/ptb-core/src/ptb/core/warehouse/load.py`, add before `return written`
in `rebuild()`:

```python
    from ..identity import matches as identity_matches

    if written.get("footballdata"):
        resolved = identity_matches.resolve_footballdata(con)
        log.info("resolved %d football-data matches into dim_match", resolved)
```

- [ ] **Step 6: Add the `coverage` command**

In `cli.py`'s `build_parser`, before `return parser`:

```python
    subparsers.add_parser("coverage", help="competition x season x source grid")
```

and the handler, extending `_HANDLERS`:

```python
def _cmd_coverage(args) -> int:
    from . import config
    from .warehouse import db

    # A read-only connect fails outright if the file is absent, so check first
    # rather than letting duckdb raise at the user.
    if not config.DB_PATH.is_file():
        print("no warehouse yet -- run `ptb ingest` then `ptb rebuild`")
        return 1

    con = db.connect(read_only=True)
    try:
        rows = con.execute(
            "SELECT 'footballdata' AS source, competition, season, count(*) AS matches "
            "FROM src_footballdata_match GROUP BY 1, 2, 3 ORDER BY 2, 3"
        ).fetchall()
        unresolved = con.execute("SELECT count(*) FROM unresolved_match").fetchone()[0]
    finally:
        con.close()

    if not rows:
        print("warehouse is empty -- run `ptb ingest` then `ptb rebuild`")
        return 1

    print("{:<14} {:<6} {:<9} {:>8}".format("SOURCE", "COMP", "SEASON", "MATCHES"))
    for source, competition, season, count in rows:
        print("{:<14} {:<6} {:<9} {:>8}".format(source, competition, season, count))
    if unresolved:
        print("\n{} unresolved match(es) -- see the unresolved_match table".format(unresolved))
    return 0


_HANDLERS = {"ingest": _cmd_ingest, "rebuild": _cmd_rebuild, "coverage": _cmd_coverage}
```

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest -v`
Expected: all tests pass (10 new in this task, 60 or so overall)

- [ ] **Step 8: Verify end to end**

```bash
rm -f data/ptb.duckdb
uv run ptb rebuild
uv run ptb coverage
```

Expected: a grid of competition/season/match counts, and no unresolved matches
for football-data (single-source ingest cannot be ambiguous).

- [ ] **Step 9: Commit**

```bash
git add packages/ptb-core/src/ptb/core/ tests/test_identity_matches.py
git commit -m "Resolve matches on a 36-hour window, not an exact date"
```

---

## Verification

After Task 8, this sequence should work from a clean checkout:

```bash
uv sync
uv run pytest
uv run ptb ingest footballdata --competition E0,SP1,I1,D1,F1 --season 2024/25
uv run ptb rebuild
uv run ptb coverage
```

Then confirm the disposability claim — the property everything else rests on:

```bash
rm data/ptb.duckdb && uv run ptb rebuild && uv run ptb coverage
```

The grid must be identical, with no network access during `rebuild`.

## Deliberate deviations from the spec

**`dim_season` is not built.** The spec lists it among the conformed dimensions,
but a season is a label (`2024/25`) with no attributes worth a table and no
foreign key that buys anything. It stays a `TEXT` column on `dim_match` and the
`src_*` tables. If seasons later need attributes — start and end dates, a
points-per-win rule, a competition format — that is the moment to add the table,
not before.

## What comes next

- **Plan 2 — remaining sources:** FotMob, Understat, ASA, StatsBomb open data.
  Each is a `Source` plus a loader plus a golden-payload fixture, following the
  football-data shape. FotMob and ASA are what bring NWSL and WSL in.
- **Plan 3 — conformed layer:** the precedence table, `v_match` / `v_shot` /
  `v_player_match`, player identity (`map_player_source` with derived confidence
  plus a committed override file), and `ptb status` / `ptb verify`.

Player identity is deliberately deferred to Plan 3: it needs at least two
sources carrying player data before the derived-mapping approach can be tested
against anything real, and football-data has no player-level data at all.
