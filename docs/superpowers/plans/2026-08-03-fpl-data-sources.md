# FPL Data Sources — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port the FPL API and the vaastav historical backfill into the park-the-bus data layer, proven by resolving a Premier League fixture onto the same `dim_match` that already carries football-data and Understat.

**Architecture:** Each source follows the Plan 1/2 shape: a `Source` that fetches verbatim payloads into the archive (`ptb ingest`), a loader that replays them into source-faithful `src_fpl_*` tables (`ptb rebuild`), and `resolve_fpl` mapping FPL fixtures into `dim_match`. Fetching and loading never mix. The FPL API serves only the current season (append-only snapshots, always fetched); vaastav supplies static history (incremental, `--refetch` to re-pull). Player identity is deferred.

**Tech Stack:** Python 3.12+, `uv` workspace, DuckDB, `requests`, `pytest`.

**Spec:** `docs/superpowers/specs/2026-08-03-fpl-data-sources-design.md`

## Global Constraints

- Python `>=3.12`. Unchanged from Plans 1-2.
- Seasons stored as `2024/25`. The FPL API's current season is derived from bootstrap event deadlines; vaastav's `2024-25` dash form converts at load time.
- `RawArchive`'s public API takes and returns **string keys**, never `pathlib.Path`.
- Fetching and loading never occur in the same command.
- Every loader is idempotent (all use `INSERT OR REPLACE`).
- Nothing is merged across sources. `src_*` tables stay source-faithful.
- Archived payloads are verbatim, self-contained envelopes.
- `data/` is gitignored.
- Identity-resolution failures produce recorded unresolved rows, never dropped data.
- The FPL API is mutable current-season data, so its ingest always fetches (append-only snapshots). Only vaastav is incremental. `--refetch` is accepted by both; FPL ignores it.

## Confirmed source facts (probed live 2026-08-03)

**FPL API** — base `https://fantasy.premierleague.com/api`, public, no auth.
- `GET /bootstrap-static/` → `{events, teams, element_types, elements, ...}`.
  - `events[]`: `{id, name, deadline_time, finished, is_current, is_next, average_entry_score, highest_score}`.
  - `teams[]`: `{id, name, short_name, strength, strength_overall_home, strength_overall_away, strength_attack_home, strength_attack_away, strength_defence_home, strength_defence_away}`.
  - `element_types[]`: `{id, singular_name, singular_name_short}` (1 GKP, 2 DEF, 3 MID, 4 FWD).
  - `elements[]` (105 fields): core `{id, web_name, first_name, second_name, team, element_type, now_cost, total_points, form, selected_by_percent, status, minutes, goals_scored, assists, clean_sheets, bonus, bps, expected_goals, expected_assists, expected_goal_involvements, code}`.
- `GET /fixtures/` → `[{id, code, event, kickoff_time, team_h, team_a, team_h_score, team_a_score, finished, team_h_difficulty, team_a_difficulty}]`.
- `GET /element-summary/{id}/` → `{fixtures, history, history_past}`. `history[]` rows (per player × gameweek): `{element, fixture, opponent_team, round, minutes, total_points, goals_scored, assists, clean_sheets, goals_conceded, own_goals, penalties_saved, penalties_missed, yellow_cards, red_cards, saves, bonus, bps, influence, creativity, threat, ict_index, expected_goals, expected_assists, expected_goal_involvements, expected_goals_conceded, value, selected, transfers_balance, transfers_in, transfers_out, was_home, kickoff_time, team_h_score, team_a_score, starts}`. Empty before a season's first gameweek is played.

**vaastav backfill** — base `https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data`.
- Seasons `2016-17` … `2025-26` (dash form; `2024-25` → `2024/25`).
- `{base}/{season}/players_raw.csv` (103 cols): `id` (season element id), `code` (stable player code), `first_name`, `second_name`, `web_name`, `element_type`, `team`, `team_code`, `now_cost`, `total_points`, `minutes`.
- `{base}/{season}/gws/merged_gw.csv` (49 cols): `name`, `position`, `team`, `element`, `fixture`, `opponent_team`, `round`, `minutes`, `total_points`, `goals_scored`, `assists`, `bonus`, `bps`, `expected_goals`, `value`, `selected`, `starts`, `was_home`, `kickoff_time`, `team_h_score`, `team_a_score`. Older seasons have fewer columns; read by name and tolerate absence.

---

## File Structure

| File | Responsibility |
|---|---|
| `.../ptb/core/sources/fpl.py` | FPL API fetch (bootstrap, fixtures, element sweep), `fpl` source |
| `.../ptb/core/sources/vaastav.py` | vaastav CSV fetch per season, incremental, `vaastav` source |
| `.../ptb/core/warehouse/schema.sql` | Add `src_fpl_*` tables |
| `.../ptb/core/warehouse/loaders/fpl.py` | FPL envelopes → `src_fpl_team/position/event/element/fixture/player_gw` |
| `.../ptb/core/warehouse/loaders/vaastav.py` | vaastav envelopes → `src_fpl_player_gw` / `src_fpl_element_season` |
| `.../ptb/core/identity/matches.py` | Add `resolve_fpl` |
| `.../ptb/core/warehouse/load.py` | Call `resolve_fpl` in `rebuild()` |
| `.../ptb/core/cli.py` | Register sources (automatic); generalize `coverage` for FPL fixtures |
| `tests/fixtures/fpl_*.json`, `vaastav_*.json` | Golden payloads |
| `tests/test_fpl_source.py`, `test_fpl_loader.py` | FPL tests |
| `tests/test_vaastav_source.py`, `test_vaastav_loader.py` | vaastav tests |
| `tests/test_identity_fpl.py` | Cross-source fixture resolution test |

---

### Task 1: FPL API source and `ptb ingest fpl`

**Files:**
- Create: `packages/ptb-core/src/ptb/core/sources/fpl.py`
- Modify: `packages/ptb-core/src/ptb/core/sources/__init__.py` (import for registration)
- Test: `tests/test_fpl_source.py`

**Interfaces:**
- Consumes: `RawArchive.write`, `register_source`
- Produces:
  - `season_from_events(events: list[dict]) -> str` (e.g. `"2024/25"`)
  - `FplSource.ingest(archive, captured_at=None, **_) -> list[str]`
  - Envelope shapes: `bootstrap` `{"source","endpoint":"bootstrap","season","url","fetched_at","data"}`; `fixtures` `{...,"endpoint":"fixtures","season",...}`; `element` `{...,"endpoint":"element","season","element_id","data"}`

- [ ] **Step 1: Write the failing test**

`tests/test_fpl_source.py`:

```python
import datetime as dt

import pytest

from ptb.core.archive import LocalBackend, RawArchive
from ptb.core.sources import fpl


def test_season_from_events_uses_first_deadline_year():
    events = [{"id": 1, "deadline_time": "2024-08-16T17:30:00Z"},
              {"id": 38, "deadline_time": "2025-05-25T14:00:00Z"}]
    assert fpl.season_from_events(events) == "2024/25"


def _bootstrap():
    return {
        "events": [{"id": 1, "deadline_time": "2024-08-16T17:30:00Z"}],
        "teams": [{"id": 1, "name": "Arsenal"}],
        "element_types": [{"id": 1, "singular_name_short": "GKP"}],
        "elements": [{"id": 11, "web_name": "Raya", "team": 1},
                     {"id": 12, "web_name": "Saka", "team": 1}],
    }


def test_ingest_archives_bootstrap_fixtures_and_each_element(tmp_path, monkeypatch):
    archive = RawArchive(LocalBackend(tmp_path))
    monkeypatch.setattr(fpl, "_fetch_bootstrap", lambda s: _bootstrap())
    monkeypatch.setattr(fpl, "_fetch_fixtures", lambda s: [{"id": 1, "event": 1}])
    monkeypatch.setattr(fpl, "_fetch_element", lambda s, eid: {"history": [], "element": eid})

    keys = fpl.FplSource().ingest(archive, captured_at=dt.datetime(2026, 8, 3, 12, 0, 0))

    assert any(k.startswith("fpl/bootstrap/2024-25__") for k in keys)
    assert any(k.startswith("fpl/fixtures/2024-25__") for k in keys)
    assert any(k.startswith("fpl/element/11__") for k in keys)
    assert any(k.startswith("fpl/element/12__") for k in keys)

    boot = archive.read(next(k for k in keys if "/bootstrap/" in k))
    assert boot["season"] == "2024/25"
    assert boot["data"]["teams"][0]["name"] == "Arsenal"

    el = archive.read(next(k for k in keys if "/element/11__" in k))
    assert el["element_id"] == 11
    assert el["season"] == "2024/25"


def test_ingest_always_refetches_elements(tmp_path, monkeypatch):
    """FPL is live current-season data: every run captures a fresh snapshot,
    so elements are fetched even when the archive already has them."""
    archive = RawArchive(LocalBackend(tmp_path))
    monkeypatch.setattr(fpl, "_fetch_bootstrap", lambda s: _bootstrap())
    monkeypatch.setattr(fpl, "_fetch_fixtures", lambda s: [])
    calls = []
    monkeypatch.setattr(fpl, "_fetch_element",
                        lambda s, eid: calls.append(eid) or {"history": []})

    fpl.FplSource().ingest(archive, captured_at=dt.datetime(2026, 8, 3, 12, 0, 0))
    fpl.FplSource().ingest(archive, captured_at=dt.datetime(2026, 8, 3, 13, 0, 0))

    assert calls == [11, 12, 11, 12]  # both players fetched on both runs
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_fpl_source.py -v`
Expected: FAIL — `ImportError: cannot import name 'fpl'`

- [ ] **Step 3: Write fpl.py**

`packages/ptb-core/src/ptb/core/sources/fpl.py`:

```python
"""FPL API -- the official Fantasy Premier League public endpoints.

Three payloads per run: one bootstrap-static (players, teams, events,
positions), one fixtures list, and one element-summary per player. The API
serves only the current season and its data mutates every gameweek, so every
run fetches a fresh, timestamped snapshot -- the archive is append-only, and a
rebuild simply loads the latest snapshot per key. There is no incremental skip;
skipping already-archived players would miss each new gameweek's history.
"""
from __future__ import annotations

import datetime as dt
import logging
import random
import time
from typing import Any, Dict, List, Optional

import requests

from .. import config
from ..archive import RawArchive
from .registry import register_source

log = logging.getLogger(__name__)

BASE = "https://fantasy.premierleague.com/api"
_POLITENESS_SECONDS = 0.3


def season_from_events(events: List[Dict[str, Any]]) -> str:
    """Derive '2024/25' from the earliest event deadline year."""
    years = [int(e["deadline_time"][:4]) for e in events if e.get("deadline_time")]
    start = min(years) if years else dt.date.today().year
    return "{}/{:02d}".format(start, (start + 1) % 100)


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": config.USER_AGENT})
    return session


def _get_json(session: requests.Session, path: str, retries: int = 3) -> Any:
    url = "{}/{}".format(BASE, path)
    last: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            resp = session.get(url, timeout=config.REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as exc:
            last = exc
            if attempt < retries:
                time.sleep((2 ** attempt) + random.random())
    raise RuntimeError("failed to fetch {}: {}".format(url, last))


def _fetch_bootstrap(session: requests.Session) -> Dict[str, Any]:
    return _get_json(session, "bootstrap-static/")


def _fetch_fixtures(session: requests.Session) -> List[Dict[str, Any]]:
    return _get_json(session, "fixtures/")


def _fetch_element(session: requests.Session, element_id: int) -> Dict[str, Any]:
    return _get_json(session, "element-summary/{}/".format(element_id))


@register_source
class FplSource:
    name = "fpl"

    def ingest(
        self,
        archive: RawArchive,
        captured_at: Optional[dt.datetime] = None,
        **_ignored,
    ) -> List[str]:
        captured_at = captured_at or dt.datetime.utcnow().replace(microsecond=0)
        session = _session()

        bootstrap = _fetch_bootstrap(session)
        season = season_from_events(bootstrap.get("events", []))
        label = season.replace("/", "-")
        stamp = captured_at.isoformat() + "Z"
        written: List[str] = []

        written.append(archive.write(
            self.name, "bootstrap",
            {"source": self.name, "endpoint": "bootstrap", "season": season,
             "url": "{}/bootstrap-static/".format(BASE), "fetched_at": stamp,
             "data": bootstrap},
            label=label, captured_at=captured_at))

        fixtures = _fetch_fixtures(session)
        written.append(archive.write(
            self.name, "fixtures",
            {"source": self.name, "endpoint": "fixtures", "season": season,
             "url": "{}/fixtures/".format(BASE), "fetched_at": stamp,
             "data": fixtures},
            label=label, captured_at=captured_at))
        time.sleep(_POLITENESS_SECONDS)

        for element in bootstrap.get("elements", []):
            eid = element["id"]
            data = _fetch_element(session, eid)
            written.append(archive.write(
                self.name, "element",
                {"source": self.name, "endpoint": "element", "season": season,
                 "element_id": eid, "url": "{}/element-summary/{}/".format(BASE, eid),
                 "fetched_at": stamp, "data": data},
                label=str(eid), captured_at=captured_at))
            time.sleep(_POLITENESS_SECONDS)

        log.info("archived fpl %s: bootstrap + fixtures + %d elements",
                 season, len(bootstrap.get("elements", [])))
        return written
```

- [ ] **Step 4: Register the module**

Append to `packages/ptb-core/src/ptb/core/sources/__init__.py`:

```python
from . import fpl  # noqa: F401,E402  -- imported for registration side effect
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_fpl_source.py -v`
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add packages/ptb-core/src/ptb/core/sources/ tests/test_fpl_source.py
git commit -m "Ingest the FPL API into the archive"
```

---

### Task 2: FPL bootstrap loader

**Files:**
- Modify: `packages/ptb-core/src/ptb/core/warehouse/schema.sql` (`src_fpl_team`, `src_fpl_position`, `src_fpl_event`, `src_fpl_element`)
- Create: `packages/ptb-core/src/ptb/core/warehouse/loaders/fpl.py`
- Modify: `packages/ptb-core/src/ptb/core/warehouse/loaders/__init__.py`
- Create: `tests/fixtures/fpl_bootstrap_sample.json`
- Test: `tests/test_fpl_loader.py` (bootstrap portion)

**Interfaces:**
- Consumes: `LOADERS` from `warehouse.load`
- Produces: `load_fpl(con, payload, archive_key) -> int` routed on `payload["endpoint"]`, registered `LOADERS["fpl"]`

- [ ] **Step 1: Extend the schema**

Append to `packages/ptb-core/src/ptb/core/warehouse/schema.sql`:

```sql
-- -------------------------------------------------------------------- fpl

CREATE TABLE IF NOT EXISTS src_fpl_team (
    season                 TEXT NOT NULL,
    team_id                INTEGER NOT NULL,
    name                   TEXT NOT NULL,
    short_name             TEXT,
    strength               INTEGER,
    strength_overall_home  INTEGER,
    strength_overall_away  INTEGER,
    strength_attack_home   INTEGER,
    strength_attack_away   INTEGER,
    strength_defence_home  INTEGER,
    strength_defence_away  INTEGER,
    archive_key            TEXT NOT NULL,
    PRIMARY KEY (season, team_id)
);

CREATE TABLE IF NOT EXISTS src_fpl_position (
    position_id  INTEGER PRIMARY KEY,
    singular_name TEXT,
    short_name    TEXT
);

CREATE TABLE IF NOT EXISTS src_fpl_event (
    season              TEXT NOT NULL,
    event_id            INTEGER NOT NULL,
    name                TEXT,
    deadline_time       TIMESTAMP,
    finished            BOOLEAN,
    is_current          BOOLEAN,
    is_next             BOOLEAN,
    average_entry_score INTEGER,
    highest_score       INTEGER,
    archive_key         TEXT NOT NULL,
    PRIMARY KEY (season, event_id)
);

CREATE TABLE IF NOT EXISTS src_fpl_element (
    season                     TEXT NOT NULL,
    element_id                 INTEGER NOT NULL,
    code                       INTEGER,
    web_name                   TEXT,
    first_name                 TEXT,
    second_name                TEXT,
    team                       INTEGER,
    element_type               INTEGER,
    now_cost                   INTEGER,
    total_points               INTEGER,
    form                       DOUBLE,
    selected_by_percent        DOUBLE,
    status                     TEXT,
    minutes                    INTEGER,
    goals_scored               INTEGER,
    assists                    INTEGER,
    clean_sheets               INTEGER,
    bonus                      INTEGER,
    bps                        INTEGER,
    expected_goals             DOUBLE,
    expected_assists           DOUBLE,
    expected_goal_involvements DOUBLE,
    archive_key                TEXT NOT NULL,
    PRIMARY KEY (season, element_id)
);
```

- [ ] **Step 2: Create the golden fixture**

`tests/fixtures/fpl_bootstrap_sample.json`:

```json
{
  "source": "fpl", "endpoint": "bootstrap", "season": "2024/25",
  "url": "https://fantasy.premierleague.com/api/bootstrap-static/",
  "fetched_at": "2026-08-03T12:00:00Z",
  "data": {
    "events": [
      {"id": 1, "name": "Gameweek 1", "deadline_time": "2024-08-16T17:30:00Z",
       "finished": true, "is_current": false, "is_next": false,
       "average_entry_score": 57, "highest_score": 127}
    ],
    "teams": [
      {"id": 1, "name": "Arsenal", "short_name": "ARS", "strength": 4,
       "strength_overall_home": 1300, "strength_overall_away": 1310,
       "strength_attack_home": 1290, "strength_attack_away": 1300,
       "strength_defence_home": 1280, "strength_defence_away": 1290},
      {"id": 20, "name": "Wolves", "short_name": "WOL", "strength": 2,
       "strength_overall_home": 1100, "strength_overall_away": 1080,
       "strength_attack_home": 1090, "strength_attack_away": 1070,
       "strength_defence_home": 1085, "strength_defence_away": 1075}
    ],
    "element_types": [
      {"id": 1, "singular_name": "Goalkeeper", "singular_name_short": "GKP"},
      {"id": 3, "singular_name": "Midfielder", "singular_name_short": "MID"}
    ],
    "elements": [
      {"id": 11, "code": 223094, "web_name": "Saka", "first_name": "Bukayo",
       "second_name": "Saka", "team": 1, "element_type": 3, "now_cost": 100,
       "total_points": 200, "form": "5.5", "selected_by_percent": "40.2",
       "status": "a", "minutes": 3000, "goals_scored": 12, "assists": 15,
       "clean_sheets": 10, "bonus": 25, "bps": 700, "expected_goals": "10.5",
       "expected_assists": "12.1", "expected_goal_involvements": "22.6"}
    ]
  }
}
```

- [ ] **Step 3: Write the failing test**

`tests/test_fpl_loader.py`:

```python
import datetime as dt
import json
from pathlib import Path

import pytest

from ptb.core.warehouse import db
from ptb.core.warehouse.loaders import fpl as loader

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def con(tmp_path):
    connection = db.connect(tmp_path / "test.duckdb")
    yield connection
    connection.close()


def _load(con, name):
    payload = json.loads((FIX / name).read_text(encoding="utf-8"))
    return loader.load_fpl(con, payload, "fpl/" + name)


def test_fpl_is_registered():
    from ptb.core.warehouse.load import LOADERS
    assert "fpl" in LOADERS


def test_bootstrap_loads_teams_positions_events_elements(con):
    _load(con, "fpl_bootstrap_sample.json")
    assert con.execute("SELECT count(*) FROM src_fpl_team").fetchone()[0] == 2
    assert con.execute("SELECT count(*) FROM src_fpl_position").fetchone()[0] == 2
    assert con.execute("SELECT count(*) FROM src_fpl_event").fetchone()[0] == 1
    assert con.execute("SELECT count(*) FROM src_fpl_element").fetchone()[0] == 1


def test_bootstrap_maps_team_and_element_columns(con):
    _load(con, "fpl_bootstrap_sample.json")
    team = con.execute(
        "SELECT name, short_name, strength_overall_home FROM src_fpl_team "
        "WHERE season = '2024/25' AND team_id = 1"
    ).fetchone()
    assert team == ("Arsenal", "ARS", 1300)

    el = con.execute(
        "SELECT web_name, team, element_type, now_cost, total_points, "
        "       selected_by_percent, expected_goals, code "
        "FROM src_fpl_element WHERE season = '2024/25' AND element_id = 11"
    ).fetchone()
    assert el[0] == "Saka"
    assert (el[1], el[2], el[3], el[4]) == (1, 3, 100, 200)
    assert el[5] == pytest.approx(40.2)
    assert el[6] == pytest.approx(10.5)
    assert el[7] == 223094


def test_bootstrap_is_idempotent(con):
    _load(con, "fpl_bootstrap_sample.json")
    _load(con, "fpl_bootstrap_sample.json")
    assert con.execute("SELECT count(*) FROM src_fpl_team").fetchone()[0] == 2
    assert con.execute("SELECT count(*) FROM src_fpl_element").fetchone()[0] == 1
```

- [ ] **Step 4: Run test to verify it fails**

Run: `uv run pytest tests/test_fpl_loader.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ptb.core.warehouse.loaders.fpl'`

- [ ] **Step 5: Write the loader**

`packages/ptb-core/src/ptb/core/warehouse/loaders/fpl.py`:

```python
"""FPL API payloads -> src_fpl_* tables, routed on payload["endpoint"].

- "bootstrap" -> src_fpl_team / src_fpl_position / src_fpl_event / src_fpl_element
- "fixtures"  -> src_fpl_fixture            (Task 3)
- "element"   -> src_fpl_player_gw          (Task 4)

FPL sends percentages and expected stats as strings; each numeric is coerced
defensively.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any, Optional

import duckdb

from ..load import LOADERS

log = logging.getLogger(__name__)


def _f(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _i(value: Any) -> Optional[int]:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _ts(value: Any) -> Optional[dt.datetime]:
    if not value:
        return None
    text = str(value).replace("Z", "").strip()
    try:
        return dt.datetime.fromisoformat(text)
    except ValueError:
        return None


def _load_bootstrap(con, payload, archive_key) -> int:
    season = payload["season"]
    data = payload["data"]

    teams = [[season, t["id"], t.get("name"), t.get("short_name"), _i(t.get("strength")),
              _i(t.get("strength_overall_home")), _i(t.get("strength_overall_away")),
              _i(t.get("strength_attack_home")), _i(t.get("strength_attack_away")),
              _i(t.get("strength_defence_home")), _i(t.get("strength_defence_away")),
              archive_key] for t in data.get("teams", []) if t.get("id") is not None]
    if teams:
        con.executemany(
            "INSERT OR REPLACE INTO src_fpl_team (season, team_id, name, short_name, "
            "strength, strength_overall_home, strength_overall_away, strength_attack_home, "
            "strength_attack_away, strength_defence_home, strength_defence_away, archive_key) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", teams)

    positions = [[p["id"], p.get("singular_name"), p.get("singular_name_short")]
                 for p in data.get("element_types", []) if p.get("id") is not None]
    if positions:
        con.executemany(
            "INSERT OR REPLACE INTO src_fpl_position (position_id, singular_name, short_name) "
            "VALUES (?, ?, ?)", positions)

    events = [[season, e["id"], e.get("name"), _ts(e.get("deadline_time")),
               e.get("finished"), e.get("is_current"), e.get("is_next"),
               _i(e.get("average_entry_score")), _i(e.get("highest_score")), archive_key]
              for e in data.get("events", []) if e.get("id") is not None]
    if events:
        con.executemany(
            "INSERT OR REPLACE INTO src_fpl_event (season, event_id, name, deadline_time, "
            "finished, is_current, is_next, average_entry_score, highest_score, archive_key) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", events)

    elements = [[season, e["id"], _i(e.get("code")), e.get("web_name"), e.get("first_name"),
                 e.get("second_name"), _i(e.get("team")), _i(e.get("element_type")),
                 _i(e.get("now_cost")), _i(e.get("total_points")), _f(e.get("form")),
                 _f(e.get("selected_by_percent")), e.get("status"), _i(e.get("minutes")),
                 _i(e.get("goals_scored")), _i(e.get("assists")), _i(e.get("clean_sheets")),
                 _i(e.get("bonus")), _i(e.get("bps")), _f(e.get("expected_goals")),
                 _f(e.get("expected_assists")), _f(e.get("expected_goal_involvements")),
                 archive_key] for e in data.get("elements", []) if e.get("id") is not None]
    if elements:
        con.executemany(
            "INSERT OR REPLACE INTO src_fpl_element (season, element_id, code, web_name, "
            "first_name, second_name, team, element_type, now_cost, total_points, form, "
            "selected_by_percent, status, minutes, goals_scored, assists, clean_sheets, "
            "bonus, bps, expected_goals, expected_assists, expected_goal_involvements, "
            "archive_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "?, ?, ?, ?, ?)", elements)

    return len(teams) + len(positions) + len(events) + len(elements)


def load_fpl(con: duckdb.DuckDBPyConnection, payload: dict, archive_key: str) -> int:
    endpoint = payload.get("endpoint")
    if endpoint == "bootstrap":
        return _load_bootstrap(con, payload, archive_key)
    if endpoint == "fixtures":
        from .fpl_fixtures import load_fixtures
        return load_fixtures(con, payload, archive_key)
    if endpoint == "element":
        from .fpl_fixtures import load_element_history
        return load_element_history(con, payload, archive_key)
    log.warning("unknown fpl endpoint %r in %s", endpoint, archive_key)
    return 0


LOADERS["fpl"] = load_fpl
```

`packages/ptb-core/src/ptb/core/warehouse/loaders/__init__.py` — append:

```python
from . import fpl  # noqa: F401  -- registers the fpl loader
```

Note: `fpl_fixtures.load_fixtures` / `load_element_history` are created in Tasks 3 and 4; the imports sit inside their branches so this task loads cleanly.

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_fpl_loader.py -v`
Expected: 4 passed

- [ ] **Step 7: Commit**

```bash
git add packages/ptb-core/src/ptb/core/warehouse/ tests/fixtures/fpl_bootstrap_sample.json tests/test_fpl_loader.py
git commit -m "Load the FPL bootstrap into the warehouse"
```

---

### Task 3: FPL fixtures loader

**Files:**
- Modify: `packages/ptb-core/src/ptb/core/warehouse/schema.sql` (`src_fpl_fixture`)
- Create: `packages/ptb-core/src/ptb/core/warehouse/loaders/fpl_fixtures.py`
- Create: `tests/fixtures/fpl_fixtures_sample.json`
- Test: `tests/test_fpl_loader.py` (add fixtures tests)

**Interfaces:**
- Consumes: `payload["endpoint"] == "fixtures"` envelope
- Produces: `load_fixtures(con, payload, archive_key) -> int`

- [ ] **Step 1: Extend the schema**

Append to `packages/ptb-core/src/ptb/core/warehouse/schema.sql`:

```sql
CREATE TABLE IF NOT EXISTS src_fpl_fixture (
    season            TEXT NOT NULL,
    fixture_id        INTEGER NOT NULL,
    code              INTEGER,
    event             INTEGER,
    kickoff_time      TIMESTAMP,
    team_h            INTEGER,
    team_a            INTEGER,
    team_h_score      INTEGER,
    team_a_score      INTEGER,
    finished          BOOLEAN,
    team_h_difficulty INTEGER,
    team_a_difficulty INTEGER,
    archive_key       TEXT NOT NULL,
    PRIMARY KEY (season, fixture_id)
);
```

- [ ] **Step 2: Create the golden fixture**

`tests/fixtures/fpl_fixtures_sample.json`:

```json
{
  "source": "fpl", "endpoint": "fixtures", "season": "2024/25",
  "url": "https://fantasy.premierleague.com/api/fixtures/",
  "fetched_at": "2026-08-03T12:00:00Z",
  "data": [
    {"id": 1, "code": 2444470, "event": 1, "kickoff_time": "2024-08-17T14:00:00Z",
     "team_h": 1, "team_a": 20, "team_h_score": 2, "team_a_score": 0,
     "finished": true, "team_h_difficulty": 2, "team_a_difficulty": 4},
    {"id": 2, "code": 2444471, "event": 1, "kickoff_time": null,
     "team_h": 5, "team_a": 6, "team_h_score": null, "team_a_score": null,
     "finished": false, "team_h_difficulty": 3, "team_a_difficulty": 3}
  ]
}
```

- [ ] **Step 3: Add the failing tests**

Append to `tests/test_fpl_loader.py`:

```python
def test_fixtures_loader_writes_rows(con):
    assert _load(con, "fpl_fixtures_sample.json") == 2
    assert con.execute("SELECT count(*) FROM src_fpl_fixture").fetchone()[0] == 2


def test_fixtures_loader_maps_columns(con):
    _load(con, "fpl_fixtures_sample.json")
    row = con.execute(
        "SELECT event, kickoff_time, team_h, team_a, team_h_score, team_a_score, "
        "       finished, team_h_difficulty FROM src_fpl_fixture "
        "WHERE season = '2024/25' AND fixture_id = 1"
    ).fetchone()
    assert row[0] == 1
    assert row[1] == dt.datetime(2024, 8, 17, 14, 0)
    assert (row[2], row[3]) == (1, 20)
    assert (row[4], row[5]) == (2, 0)
    assert row[6] is True
    assert row[7] == 2


def test_fixtures_loader_is_idempotent(con):
    _load(con, "fpl_fixtures_sample.json")
    _load(con, "fpl_fixtures_sample.json")
    assert con.execute("SELECT count(*) FROM src_fpl_fixture").fetchone()[0] == 2
```

- [ ] **Step 4: Run test to verify it fails**

Run: `uv run pytest tests/test_fpl_loader.py -k fixtures -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ptb.core.warehouse.loaders.fpl_fixtures'`

- [ ] **Step 5: Write the loader**

`packages/ptb-core/src/ptb/core/warehouse/loaders/fpl_fixtures.py`:

```python
"""FPL fixtures and element-summary history -> src_fpl_fixture / src_fpl_player_gw."""
from __future__ import annotations

import datetime as dt
from typing import Any, List, Optional

import duckdb


def _f(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _i(value: Any) -> Optional[int]:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _ts(value: Any) -> Optional[dt.datetime]:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "").strip())
    except ValueError:
        return None


_FIXTURE_COLUMNS = [
    "season", "fixture_id", "code", "event", "kickoff_time", "team_h", "team_a",
    "team_h_score", "team_a_score", "finished", "team_h_difficulty",
    "team_a_difficulty", "archive_key",
]


def load_fixtures(con: duckdb.DuckDBPyConnection, payload: dict, archive_key: str) -> int:
    season = payload["season"]
    rows = []
    for fx in payload["data"]:
        if fx.get("id") is None:
            continue
        rows.append([
            season, _i(fx.get("id")), _i(fx.get("code")), _i(fx.get("event")),
            _ts(fx.get("kickoff_time")), _i(fx.get("team_h")), _i(fx.get("team_a")),
            _i(fx.get("team_h_score")), _i(fx.get("team_a_score")), fx.get("finished"),
            _i(fx.get("team_h_difficulty")), _i(fx.get("team_a_difficulty")), archive_key,
        ])
    if rows:
        con.executemany(
            "INSERT OR REPLACE INTO src_fpl_fixture ({}) VALUES ({})".format(
                ", ".join(_FIXTURE_COLUMNS), ", ".join("?" * len(_FIXTURE_COLUMNS))),
            rows)
    return len(rows)


# --- element-summary history (Task 4 adds the schema and tests) --------------

_GW_COLUMNS = [
    "season", "source", "element_id", "event", "fixture", "opponent_team", "minutes",
    "total_points", "goals_scored", "assists", "clean_sheets", "goals_conceded",
    "own_goals", "penalties_saved", "penalties_missed", "yellow_cards", "red_cards",
    "saves", "bonus", "bps", "influence", "creativity", "threat", "ict_index",
    "expected_goals", "expected_assists", "expected_goal_involvements",
    "expected_goals_conceded", "value", "selected", "transfers_balance",
    "transfers_in", "transfers_out", "was_home", "kickoff_time", "team_h_score",
    "team_a_score", "starts", "player_name", "archive_key",
]


def _gw_row(season: str, source: str, element_id: Optional[int], row: dict,
            player_name: Optional[str], archive_key: str) -> list:
    return [
        season, source, element_id, _i(row.get("round")), _i(row.get("fixture")),
        _i(row.get("opponent_team")), _i(row.get("minutes")), _i(row.get("total_points")),
        _i(row.get("goals_scored")), _i(row.get("assists")), _i(row.get("clean_sheets")),
        _i(row.get("goals_conceded")), _i(row.get("own_goals")), _i(row.get("penalties_saved")),
        _i(row.get("penalties_missed")), _i(row.get("yellow_cards")), _i(row.get("red_cards")),
        _i(row.get("saves")), _i(row.get("bonus")), _i(row.get("bps")), _f(row.get("influence")),
        _f(row.get("creativity")), _f(row.get("threat")), _f(row.get("ict_index")),
        _f(row.get("expected_goals")), _f(row.get("expected_assists")),
        _f(row.get("expected_goal_involvements")), _f(row.get("expected_goals_conceded")),
        _i(row.get("value")), _i(row.get("selected")), _i(row.get("transfers_balance")),
        _i(row.get("transfers_in")), _i(row.get("transfers_out")), row.get("was_home"),
        _ts(row.get("kickoff_time")), _i(row.get("team_h_score")), _i(row.get("team_a_score")),
        _i(row.get("starts")), player_name, archive_key,
    ]


def _insert_gw(con: duckdb.DuckDBPyConnection, rows: List[list]) -> int:
    if rows:
        con.executemany(
            "INSERT OR REPLACE INTO src_fpl_player_gw ({}) VALUES ({})".format(
                ", ".join(_GW_COLUMNS), ", ".join("?" * len(_GW_COLUMNS))),
            rows)
    return len(rows)


def load_element_history(con: duckdb.DuckDBPyConnection, payload: dict, archive_key: str) -> int:
    season = payload["season"]
    element_id = _i(payload.get("element_id"))
    history = (payload.get("data") or {}).get("history", [])
    rows = [_gw_row(season, "api", element_id, h, None, archive_key) for h in history]
    return _insert_gw(con, rows)
```

Note: `load_element_history` and the `src_fpl_player_gw` insert are exercised by Task 4's tests once the table exists; this task's tests cover fixtures only.

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_fpl_loader.py -v`
Expected: 7 passed

- [ ] **Step 7: Commit**

```bash
git add packages/ptb-core/src/ptb/core/warehouse/ tests/fixtures/fpl_fixtures_sample.json tests/test_fpl_loader.py
git commit -m "Load FPL fixtures into the warehouse"
```

---

### Task 4: FPL player-gameweek loader

**Files:**
- Modify: `packages/ptb-core/src/ptb/core/warehouse/schema.sql` (`src_fpl_player_gw`)
- Create: `tests/fixtures/fpl_element_sample.json`
- Test: `tests/test_fpl_loader.py` (add element-history tests)

**Interfaces:**
- Consumes: `payload["endpoint"] == "element"` envelope; `load_element_history` (Task 3)
- Produces: rows in `src_fpl_player_gw` with `source = 'api'`

The loader (`load_element_history`) already exists from Task 3; this task adds the
table it writes to plus the golden fixture and tests. The failing state is the
table not yet existing, so the test comes **before** the schema here.

- [ ] **Step 1: Create the golden fixture**

`tests/fixtures/fpl_element_sample.json` (a two-gameweek history — the real `history` row shape):

```json
{
  "source": "fpl", "endpoint": "element", "season": "2024/25", "element_id": 11,
  "url": "https://fantasy.premierleague.com/api/element-summary/11/",
  "fetched_at": "2026-08-03T12:00:00Z",
  "data": {
    "fixtures": [],
    "history_past": [],
    "history": [
      {"element": 11, "fixture": 1, "opponent_team": 20, "round": 1, "minutes": 90,
       "total_points": 13, "goals_scored": 1, "assists": 1, "clean_sheets": 1,
       "goals_conceded": 0, "own_goals": 0, "penalties_saved": 0, "penalties_missed": 0,
       "yellow_cards": 0, "red_cards": 0, "saves": 0, "bonus": 2, "bps": 45,
       "influence": "40.2", "creativity": "55.1", "threat": "60.0", "ict_index": "15.5",
       "expected_goals": "0.55", "expected_assists": "0.33",
       "expected_goal_involvements": "0.88", "expected_goals_conceded": "0.70",
       "value": 100, "selected": 4200000, "transfers_balance": 0, "transfers_in": 0,
       "transfers_out": 0, "was_home": true, "kickoff_time": "2024-08-17T14:00:00Z",
       "team_h_score": 2, "team_a_score": 0, "starts": 1},
      {"element": 11, "fixture": 12, "opponent_team": 5, "round": 2, "minutes": 78,
       "total_points": 6, "goals_scored": 0, "assists": 1, "clean_sheets": 0,
       "goals_conceded": 1, "own_goals": 0, "penalties_saved": 0, "penalties_missed": 0,
       "yellow_cards": 1, "red_cards": 0, "saves": 0, "bonus": 0, "bps": 25,
       "influence": "20.0", "creativity": "30.0", "threat": "18.0", "ict_index": "6.8",
       "expected_goals": "0.10", "expected_assists": "0.40",
       "expected_goal_involvements": "0.50", "expected_goals_conceded": "1.20",
       "value": 101, "selected": 4500000, "transfers_balance": 300000,
       "transfers_in": 350000, "transfers_out": 50000, "was_home": false,
       "kickoff_time": "2024-08-24T14:00:00Z", "team_h_score": 1, "team_a_score": 1,
       "starts": 1}
    ]
  }
}
```

- [ ] **Step 2: Add the failing tests**

Append to `tests/test_fpl_loader.py`:

```python
def test_element_history_loads_each_gameweek(con):
    assert _load(con, "fpl_element_sample.json") == 2
    assert con.execute(
        "SELECT count(*) FROM src_fpl_player_gw WHERE source = 'api'"
    ).fetchone()[0] == 2


def test_element_history_maps_columns(con):
    _load(con, "fpl_element_sample.json")
    row = con.execute(
        "SELECT event, fixture, opponent_team, minutes, total_points, goals_scored, "
        "       assists, bonus, bps, expected_goals, value, was_home "
        "FROM src_fpl_player_gw WHERE element_id = 11 AND event = 1 AND source = 'api'"
    ).fetchone()
    assert row[0] == 1
    assert (row[1], row[2]) == (1, 20)
    assert (row[3], row[4]) == (90, 13)
    assert (row[5], row[6]) == (1, 1)
    assert (row[7], row[8]) == (2, 45)
    assert row[9] == pytest.approx(0.55)
    assert row[10] == 100
    assert row[11] is True


def test_element_history_is_idempotent(con):
    _load(con, "fpl_element_sample.json")
    _load(con, "fpl_element_sample.json")
    assert con.execute("SELECT count(*) FROM src_fpl_player_gw").fetchone()[0] == 2
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_fpl_loader.py -k element_history -v`
Expected: FAIL — `duckdb ... Table with name src_fpl_player_gw does not exist`

- [ ] **Step 4: Extend the schema**

Append to `packages/ptb-core/src/ptb/core/warehouse/schema.sql`:

```sql
CREATE TABLE IF NOT EXISTS src_fpl_player_gw (
    season                     TEXT NOT NULL,
    source                     TEXT NOT NULL,
    element_id                 INTEGER NOT NULL,
    event                      INTEGER NOT NULL,
    fixture                    INTEGER,
    opponent_team              INTEGER,
    minutes                    INTEGER,
    total_points               INTEGER,
    goals_scored               INTEGER,
    assists                    INTEGER,
    clean_sheets               INTEGER,
    goals_conceded             INTEGER,
    own_goals                  INTEGER,
    penalties_saved            INTEGER,
    penalties_missed           INTEGER,
    yellow_cards               INTEGER,
    red_cards                  INTEGER,
    saves                      INTEGER,
    bonus                      INTEGER,
    bps                        INTEGER,
    influence                  DOUBLE,
    creativity                 DOUBLE,
    threat                     DOUBLE,
    ict_index                  DOUBLE,
    expected_goals             DOUBLE,
    expected_assists           DOUBLE,
    expected_goal_involvements DOUBLE,
    expected_goals_conceded    DOUBLE,
    value                      INTEGER,
    selected                   INTEGER,
    transfers_balance          INTEGER,
    transfers_in               INTEGER,
    transfers_out              INTEGER,
    was_home                   BOOLEAN,
    kickoff_time               TIMESTAMP,
    team_h_score               INTEGER,
    team_a_score               INTEGER,
    starts                     INTEGER,
    player_name                TEXT,
    archive_key                TEXT NOT NULL,
    PRIMARY KEY (season, source, element_id, event)
);
```

- [ ] **Step 5: Run tests to verify they pass**

The loader (`load_element_history`) already exists from Task 3; this task adds the table and the fixture. Run: `uv run pytest tests/test_fpl_loader.py -v`
Expected: 10 passed

- [ ] **Step 6: Commit**

```bash
git add packages/ptb-core/src/ptb/core/warehouse/ tests/fixtures/fpl_element_sample.json tests/test_fpl_loader.py
git commit -m "Load FPL per-gameweek player history into the warehouse"
```

---

### Task 5: FPL fixture resolution and the three-source match

**Files:**
- Modify: `packages/ptb-core/src/ptb/core/identity/matches.py` (`resolve_fpl`)
- Modify: `packages/ptb-core/src/ptb/core/warehouse/load.py` (call it in `rebuild`)
- Test: `tests/test_identity_fpl.py`

**Interfaces:**
- Consumes: `resolve_match`, `src_fpl_fixture` joined to `src_fpl_team`
- Produces: `resolve_fpl(con) -> int`

- [ ] **Step 1: Write the failing test**

`tests/test_identity_fpl.py`:

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


def _fpl_team(con, season, tid, name):
    con.execute(
        "INSERT OR REPLACE INTO src_fpl_team (season, team_id, name, archive_key) "
        "VALUES (?, ?, ?, 'k')", [season, tid, name])


def _fpl_fixture(con, season, fid, kickoff, team_h, team_a):
    con.execute(
        "INSERT INTO src_fpl_fixture (season, fixture_id, event, kickoff_time, team_h, "
        "team_a, archive_key) VALUES (?, ?, 1, ?, ?, ?, 'k')",
        [season, fid, kickoff, team_h, team_a])


def test_resolve_fpl_maps_fixtures_via_team_names(con):
    _fpl_team(con, "2024/25", 1, "Arsenal")
    _fpl_team(con, "2024/25", 20, "Wolves")
    _fpl_fixture(con, "2024/25", 1, dt.datetime(2024, 8, 17, 14, 0), 1, 20)
    resolved = matches.resolve_fpl(con)
    assert resolved == 1
    assert con.execute("SELECT count(*) FROM dim_match").fetchone()[0] == 1


def test_three_sources_resolve_to_one_match(con):
    """The headline test: football-data, Understat and FPL all land on one
    dim_match for the same Premier League fixture."""
    con.execute(
        "INSERT INTO src_footballdata_match "
        "(competition, season, match_date, kickoff_time, home_team, away_team, archive_key) "
        "VALUES ('E0', '2024/25', DATE '2024-08-17', TIME '14:00', 'Arsenal', 'Wolves', 'fd')"
    )
    matches.resolve_footballdata(con)

    con.execute(
        "INSERT INTO src_understat_match "
        "(understat_match_id, competition, season, kickoff, home_team, away_team, archive_key) "
        "VALUES ('1001', 'E0', '2024/25', TIMESTAMP '2024-08-17 15:00:00', "
        "        'Arsenal', 'Wolverhampton Wanderers', 'us')"
    )
    matches.resolve_understat(con)

    _fpl_team(con, "2024/25", 1, "Arsenal")
    _fpl_team(con, "2024/25", 20, "Wolves")
    _fpl_fixture(con, "2024/25", 1, dt.datetime(2024, 8, 17, 14, 0), 1, 20)
    matches.resolve_fpl(con)

    assert con.execute("SELECT count(*) FROM dim_match").fetchone()[0] == 1
    sources = con.execute("SELECT count(DISTINCT source) FROM map_match_source").fetchone()[0]
    assert sources == 3


def test_resolve_fpl_is_idempotent(con):
    _fpl_team(con, "2024/25", 1, "Arsenal")
    _fpl_team(con, "2024/25", 20, "Wolves")
    _fpl_fixture(con, "2024/25", 1, dt.datetime(2024, 8, 17, 14, 0), 1, 20)
    matches.resolve_fpl(con)
    matches.resolve_fpl(con)
    assert con.execute("SELECT count(*) FROM dim_match").fetchone()[0] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_identity_fpl.py -v`
Expected: FAIL — `AttributeError: module 'ptb.core.identity.matches' has no attribute 'resolve_fpl'`

- [ ] **Step 3: Write `resolve_fpl`**

Append to `packages/ptb-core/src/ptb/core/identity/matches.py`:

```python
def resolve_fpl(con: duckdb.DuckDBPyConnection) -> int:
    """Resolve every FPL fixture into dim_match. Idempotent.

    FPL fixtures reference teams by numeric id, so each is joined to
    src_fpl_team for the names resolve_match needs. Fixtures are Premier League
    matches, so they resolve against football-data and Understat rows already in
    dim_match -- the first three-source match identity in the project.
    """
    rows = con.execute(
        "SELECT f.season, f.fixture_id, f.kickoff_time, th.name, ta.name "
        "FROM src_fpl_fixture f "
        "JOIN src_fpl_team th ON th.season = f.season AND th.team_id = f.team_h "
        "JOIN src_fpl_team ta ON ta.season = f.season AND ta.team_id = f.team_a "
        "WHERE f.kickoff_time IS NOT NULL "
        "ORDER BY f.kickoff_time, f.fixture_id"
    ).fetchall()

    resolved = 0
    for season, fixture_id, kickoff, home, away in rows:
        if resolve_match(
            con, source="fpl", source_match_id="{}|{}".format(season, fixture_id),
            competition="E0", season=season, kickoff=kickoff,
            home_team=home, away_team=away,
        ) is not None:
            resolved += 1
    return resolved
```

(The alias `ta` is safe; only `at` is a DuckDB reserved word.)

- [ ] **Step 4: Wire it into `rebuild`**

In `packages/ptb-core/src/ptb/core/warehouse/load.py`, extend the identity block so it ends:

```python
    if written.get("asa"):
        resolved = identity_matches.resolve_asa(con)
        log.info("resolved %d asa games into dim_match", resolved)

    if written.get("fpl"):
        resolved = identity_matches.resolve_fpl(con)
        log.info("resolved %d fpl fixtures into dim_match", resolved)

    return written
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_identity_fpl.py -v`
Expected: 3 passed

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add packages/ptb-core/src/ptb/core/ tests/test_identity_fpl.py
git commit -m "Resolve FPL fixtures into dim_match, joining football-data and Understat"
```

---

### Task 6: vaastav backfill source

**Files:**
- Create: `packages/ptb-core/src/ptb/core/sources/vaastav.py`
- Modify: `packages/ptb-core/src/ptb/core/sources/__init__.py` (import for registration)
- Test: `tests/test_vaastav_source.py`

**Interfaces:**
- Consumes: `RawArchive.write`, `RawArchive.keys`, `register_source`
- Produces:
  - `KNOWN_SEASONS: tuple[str, ...]`, `season_label("2024-25") -> "2024/25"`
  - `archived_seasons(archive) -> set[str]`
  - `VaastavSource.ingest(archive, seasons=..., refetch=False, captured_at=None) -> list[str]`
  - Envelope: `{"source":"vaastav","endpoint":"season","season","url","fetched_at","data":{"players_raw","merged_gw"}}`

- [ ] **Step 1: Write the failing test**

`tests/test_vaastav_source.py`:

```python
import datetime as dt

import pytest

from ptb.core.archive import LocalBackend, RawArchive
from ptb.core.sources import vaastav


def test_season_label_converts_dash_form():
    assert vaastav.season_label("2024-25") == "2024/25"
    assert vaastav.season_label("2016-17") == "2016/17"


def test_ingest_archives_one_payload_per_season(tmp_path, monkeypatch):
    archive = RawArchive(LocalBackend(tmp_path))
    monkeypatch.setattr(vaastav, "_fetch_csv",
                        lambda s, url: "id,code\n1,100\n" if "players_raw" in url
                        else "element,round\n1,1\n")

    keys = vaastav.VaastavSource().ingest(
        archive, seasons=["2023-24", "2024-25"],
        captured_at=dt.datetime(2026, 8, 3, 12, 0, 0))

    assert any(k.startswith("vaastav/season/2023-24__") for k in keys)
    assert any(k.startswith("vaastav/season/2024-25__") for k in keys)
    env = archive.read(next(k for k in keys if "2024-25__" in k))
    assert env["season"] == "2024/25"
    assert env["data"]["players_raw"].startswith("id,code")
    assert env["data"]["merged_gw"].startswith("element,round")


def test_ingest_is_incremental(tmp_path, monkeypatch):
    archive = RawArchive(LocalBackend(tmp_path))
    calls = []
    monkeypatch.setattr(vaastav, "_fetch_csv",
                        lambda s, url: calls.append(url) or "a,b\n1,2\n")

    vaastav.VaastavSource().ingest(archive, seasons=["2024-25"],
                                   captured_at=dt.datetime(2026, 8, 3, 12, 0, 0))
    first = len(calls)
    vaastav.VaastavSource().ingest(archive, seasons=["2024-25"],
                                   captured_at=dt.datetime(2026, 8, 3, 13, 0, 0))
    assert len(calls) == first  # second run fetched nothing new


def test_refetch_repulls(tmp_path, monkeypatch):
    archive = RawArchive(LocalBackend(tmp_path))
    calls = []
    monkeypatch.setattr(vaastav, "_fetch_csv",
                        lambda s, url: calls.append(url) or "a,b\n1,2\n")

    vaastav.VaastavSource().ingest(archive, seasons=["2024-25"],
                                   captured_at=dt.datetime(2026, 8, 3, 12, 0, 0))
    n = len(calls)
    vaastav.VaastavSource().ingest(archive, seasons=["2024-25"], refetch=True,
                                   captured_at=dt.datetime(2026, 8, 3, 13, 0, 0))
    assert len(calls) > n
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_vaastav_source.py -v`
Expected: FAIL — `ImportError: cannot import name 'vaastav'`

- [ ] **Step 3: Write vaastav.py**

`packages/ptb-core/src/ptb/core/sources/vaastav.py`:

```python
"""vaastav/Fantasy-Premier-League -- historical FPL backfill.

The official API serves only the current season. This community repo publishes
cleaned per-gameweek CSVs back to 2016/17. One payload per season bundles
players_raw.csv and gws/merged_gw.csv, wrapped verbatim. Static history, so a
season already archived is skipped unless `refetch`.
"""
from __future__ import annotations

import datetime as dt
import logging
import random
import time
from typing import List, Optional, Sequence, Set

import requests

from .. import config
from ..archive import RawArchive
from .registry import register_source

log = logging.getLogger(__name__)

RAW_BASE = "https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data"
KNOWN_SEASONS = (
    "2016-17", "2017-18", "2018-19", "2019-20", "2020-21",
    "2021-22", "2022-23", "2023-24", "2024-25", "2025-26",
)
_POLITENESS_SECONDS = 0.5


def season_label(dash: str) -> str:
    """'2024-25' -> '2024/25'."""
    start, end = dash.split("-")
    return "{}/{}".format(start, end)


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": config.USER_AGENT})
    return session


def _fetch_csv(session: requests.Session, url: str, retries: int = 3) -> str:
    last: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            resp = session.get(url, timeout=config.REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as exc:
            last = exc
            if attempt < retries:
                time.sleep((2 ** attempt) + random.random())
    raise RuntimeError("failed to fetch {}: {}".format(url, last))


def archived_seasons(archive: RawArchive) -> Set[str]:
    prefix = "vaastav/season/"
    seasons: Set[str] = set()
    for key in archive.keys(source="vaastav", endpoint="season"):
        seasons.add(key[len(prefix):].split("__", 1)[0])
    return seasons


@register_source
class VaastavSource:
    name = "vaastav"

    def ingest(
        self,
        archive: RawArchive,
        seasons: Optional[Sequence[str]] = None,
        refetch: bool = False,
        captured_at: Optional[dt.datetime] = None,
        **_ignored,
    ) -> List[str]:
        seasons = list(seasons or KNOWN_SEASONS)
        captured_at = captured_at or dt.datetime.utcnow().replace(microsecond=0)
        session = _session()
        done = set() if refetch else archived_seasons(archive)
        written: List[str] = []

        for dash in seasons:
            if dash in done:
                continue
            players = _fetch_csv(session, "{}/{}/players_raw.csv".format(RAW_BASE, dash))
            merged = _fetch_csv(session, "{}/{}/gws/merged_gw.csv".format(RAW_BASE, dash))
            envelope = {
                "source": self.name, "endpoint": "season", "season": season_label(dash),
                "url": "{}/{}/".format(RAW_BASE, dash),
                "fetched_at": captured_at.isoformat() + "Z",
                "data": {"players_raw": players, "merged_gw": merged},
            }
            written.append(archive.write(
                self.name, "season", envelope, label=dash, captured_at=captured_at))
            log.info("archived vaastav %s", dash)
            time.sleep(_POLITENESS_SECONDS)

        return written
```

- [ ] **Step 4: Register the module**

Append to `packages/ptb-core/src/ptb/core/sources/__init__.py`:

```python
from . import vaastav  # noqa: F401,E402  -- imported for registration side effect
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_vaastav_source.py -v`
Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
git add packages/ptb-core/src/ptb/core/sources/ tests/test_vaastav_source.py
git commit -m "Ingest the vaastav FPL backfill into the archive"
```

---

### Task 7: vaastav loader

**Files:**
- Modify: `packages/ptb-core/src/ptb/core/warehouse/schema.sql` (`src_fpl_element_season`)
- Create: `packages/ptb-core/src/ptb/core/warehouse/loaders/vaastav.py`
- Modify: `packages/ptb-core/src/ptb/core/warehouse/loaders/__init__.py`
- Create: `tests/fixtures/vaastav_2024_25_sample.json`
- Test: `tests/test_vaastav_loader.py`

**Interfaces:**
- Consumes: `LOADERS`, `_insert_gw` / `_gw_row` from `fpl_fixtures`
- Produces: `load_vaastav(con, payload, archive_key) -> int`, registered `LOADERS["vaastav"]`

- [ ] **Step 1: Extend the schema**

Append to `packages/ptb-core/src/ptb/core/warehouse/schema.sql`:

```sql
CREATE TABLE IF NOT EXISTS src_fpl_element_season (
    season       TEXT NOT NULL,
    element_id   INTEGER NOT NULL,
    code         INTEGER,
    first_name   TEXT,
    second_name  TEXT,
    web_name     TEXT,
    element_type INTEGER,
    team         INTEGER,
    team_code    INTEGER,
    now_cost     INTEGER,
    total_points INTEGER,
    minutes      INTEGER,
    archive_key  TEXT NOT NULL,
    PRIMARY KEY (season, element_id)
);
```

- [ ] **Step 2: Create the golden fixture**

`tests/fixtures/vaastav_2024_25_sample.json`:

```json
{
  "source": "vaastav", "endpoint": "season", "season": "2024/25",
  "url": "https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data/2024-25/",
  "fetched_at": "2026-08-03T12:00:00Z",
  "data": {
    "players_raw": "id,code,first_name,second_name,web_name,element_type,team,team_code,now_cost,total_points,minutes\n11,223094,Bukayo,Saka,Saka,3,1,3,100,200,3000\n",
    "merged_gw": "name,position,team,element,fixture,opponent_team,round,minutes,total_points,goals_scored,assists,bonus,bps,expected_goals,value,selected,starts,was_home,kickoff_time,team_h_score,team_a_score\nBukayo Saka,MID,Arsenal,11,1,20,1,90,13,1,1,2,45,0.55,100,4200000,1,True,2024-08-17T14:00:00Z,2,0\n"
  }
}
```

- [ ] **Step 3: Write the failing test**

`tests/test_vaastav_loader.py`:

```python
import datetime as dt
import json
from pathlib import Path

import pytest

from ptb.core.warehouse import db
from ptb.core.warehouse.loaders import vaastav as loader

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def con(tmp_path):
    connection = db.connect(tmp_path / "test.duckdb")
    yield connection
    connection.close()


def _load(con):
    payload = json.loads((FIX / "vaastav_2024_25_sample.json").read_text(encoding="utf-8"))
    return loader.load_vaastav(con, payload, "vaastav/season/2024-25__k.json.gz")


def test_vaastav_is_registered():
    from ptb.core.warehouse.load import LOADERS
    assert "vaastav" in LOADERS


def test_loads_player_season_and_gw(con):
    _load(con)
    assert con.execute("SELECT count(*) FROM src_fpl_element_season").fetchone()[0] == 1
    assert con.execute(
        "SELECT count(*) FROM src_fpl_player_gw WHERE source = 'vaastav'"
    ).fetchone()[0] == 1


def test_player_season_columns(con):
    _load(con)
    row = con.execute(
        "SELECT code, web_name, element_type, team, total_points, minutes "
        "FROM src_fpl_element_season WHERE season = '2024/25' AND element_id = 11"
    ).fetchone()
    assert row == (223094, "Saka", 3, 1, 200, 3000)


def test_gw_columns_and_player_name(con):
    _load(con)
    row = con.execute(
        "SELECT event, minutes, total_points, goals_scored, assists, value, "
        "       was_home, player_name FROM src_fpl_player_gw "
        "WHERE source = 'vaastav' AND element_id = 11 AND event = 1"
    ).fetchone()
    assert row[0] == 1
    assert (row[1], row[2]) == (90, 13)
    assert (row[3], row[4]) == (1, 1)
    assert row[5] == 100
    assert row[6] is True
    assert row[7] == "Bukayo Saka"


def test_vaastav_loader_is_idempotent(con):
    _load(con)
    _load(con)
    assert con.execute("SELECT count(*) FROM src_fpl_element_season").fetchone()[0] == 1
    assert con.execute("SELECT count(*) FROM src_fpl_player_gw").fetchone()[0] == 1
```

- [ ] **Step 4: Run test to verify it fails**

Run: `uv run pytest tests/test_vaastav_loader.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ptb.core.warehouse.loaders.vaastav'`

- [ ] **Step 5: Write the loader**

`packages/ptb-core/src/ptb/core/warehouse/loaders/vaastav.py`:

```python
"""vaastav season payload -> src_fpl_element_season and src_fpl_player_gw.

Two CSVs share one envelope. players_raw is the per-season player snapshot;
merged_gw is one row per player per gameweek, reusing the same row builder as
the FPL API element-summary loader so both sources land in src_fpl_player_gw.
Columns vary by era, so every field is read by name and tolerated when absent.
"""
from __future__ import annotations

import csv
import io
from typing import Any, Optional

import duckdb

from ..load import LOADERS
from .fpl_fixtures import _gw_row, _insert_gw


def _i(value: Any) -> Optional[int]:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _get(row: dict, key: str) -> Optional[str]:
    value = row.get(key)
    if value is None:
        return None
    value = str(value).strip()
    return value or None


_SEASON_COLUMNS = [
    "season", "element_id", "code", "first_name", "second_name", "web_name",
    "element_type", "team", "team_code", "now_cost", "total_points", "minutes",
    "archive_key",
]


def _load_players_raw(con, season, csv_text, archive_key) -> int:
    rows = []
    for r in csv.DictReader(io.StringIO(csv_text)):
        eid = _i(r.get("id"))
        if eid is None:
            continue
        rows.append([
            season, eid, _i(r.get("code")), _get(r, "first_name"), _get(r, "second_name"),
            _get(r, "web_name"), _i(r.get("element_type")), _i(r.get("team")),
            _i(r.get("team_code")), _i(r.get("now_cost")), _i(r.get("total_points")),
            _i(r.get("minutes")), archive_key,
        ])
    if rows:
        con.executemany(
            "INSERT OR REPLACE INTO src_fpl_element_season ({}) VALUES ({})".format(
                ", ".join(_SEASON_COLUMNS), ", ".join("?" * len(_SEASON_COLUMNS))),
            rows)
    return len(rows)


def _load_merged_gw(con, season, csv_text, archive_key) -> int:
    rows = []
    for r in csv.DictReader(io.StringIO(csv_text)):
        eid = _i(r.get("element"))
        if eid is None or r.get("round") in (None, ""):
            continue
        # merged_gw uses 'True'/'False' strings for was_home
        home = _get(r, "was_home")
        r = dict(r)
        r["was_home"] = True if home == "True" else (False if home == "False" else None)
        rows.append(_gw_row(season, "vaastav", eid, r, _get(r, "name"), archive_key))
    return _insert_gw(con, rows)


def load_vaastav(con: duckdb.DuckDBPyConnection, payload: dict, archive_key: str) -> int:
    season = payload["season"]
    data = payload["data"]
    n = _load_players_raw(con, season, data.get("players_raw", ""), archive_key)
    n += _load_merged_gw(con, season, data.get("merged_gw", ""), archive_key)
    return n


LOADERS["vaastav"] = load_vaastav
```

`packages/ptb-core/src/ptb/core/warehouse/loaders/__init__.py` — append:

```python
from . import vaastav  # noqa: F401  -- registers the vaastav loader
```

Note: `_gw_row` coerces numerics with `_i`/`_f`, so passing CSV string values is fine; `value`, `round`, `minutes` etc. arrive as strings and are coerced. `was_home` is normalised to a real bool above because `_gw_row` stores it directly.

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_vaastav_loader.py -v`
Expected: 5 passed

- [ ] **Step 7: Commit**

```bash
git add packages/ptb-core/src/ptb/core/warehouse/ tests/fixtures/vaastav_2024_25_sample.json tests/test_vaastav_loader.py
git commit -m "Load the vaastav backfill into the warehouse"
```

---

### Task 8: FPL in `ptb coverage`

**Files:**
- Modify: `packages/ptb-core/src/ptb/core/cli.py` (`_cmd_coverage`)
- Test: `tests/test_coverage.py` (extend)

**Interfaces:**
- Consumes: `src_fpl_fixture`
- Produces: `_cmd_coverage` reporting FPL alongside the other sources

- [ ] **Step 1: Extend the failing test**

Append to `tests/test_coverage.py`'s `warehouse` fixture body, before `con.close()`:

```python
    con.execute(
        "INSERT INTO src_fpl_fixture "
        "(season, fixture_id, event, kickoff_time, team_h, team_a, archive_key) "
        "VALUES ('2024/25', 1, 1, TIMESTAMP '2024-08-17 14:00:00', 1, 20, 'k')"
    )
```

and add a test:

```python
def test_coverage_includes_fpl(warehouse):
    out = io.StringIO()
    with redirect_stdout(out):
        rc = _cmd_coverage(_Args())
    assert rc == 0
    assert "fpl" in out.getvalue()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_coverage.py::test_coverage_includes_fpl -v`
Expected: FAIL — `fpl` not in output

- [ ] **Step 3: Add FPL to the coverage union**

In `packages/ptb-core/src/ptb/core/cli.py`, in `_cmd_coverage`, add a `UNION ALL` arm before the final `ORDER BY 1, 2, 3`:

```python
            "UNION ALL "
            "SELECT 'fpl', 'E0', season, count(*) "
            "  FROM src_fpl_fixture GROUP BY 1, 2, 3 "
```

so the full query reads (football-data, understat, asa arms unchanged) with the fpl arm appended, then `"ORDER BY 1, 2, 3"`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_coverage.py -v`
Expected: 3 passed

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add packages/ptb-core/src/ptb/core/cli.py tests/test_coverage.py
git commit -m "Report FPL fixtures in ptb coverage"
```

---

## Verification

After Task 8, this sequence should work end to end:

```bash
uv run pytest

# FPL current season (bootstrap + fixtures + ~600 element sweeps -- a few minutes)
uv run ptb ingest fpl
# football-data + Understat for the same season so three-source resolution has data
uv run ptb ingest footballdata --competition E0 --season 2024/25
# vaastav one season
uv run ptb ingest vaastav --season 2024-25

uv run ptb rebuild
uv run ptb coverage
```

Then confirm the headline property -- one Premier League match carrying three sources:

```bash
uv run python -c "
from ptb.core.warehouse import db
con = db.connect(read_only=True)
n = con.execute('''
  SELECT count(*) FROM (
    SELECT match_id FROM map_match_source
    WHERE source IN ('footballdata','understat','fpl')
    GROUP BY match_id HAVING count(DISTINCT source) = 3)
''').fetchone()[0]
print('matches carrying football-data + understat + fpl:', n)
"
```

Expected: a healthy count of E0 fixtures resolving across all three sources.

And disposability -- delete the warehouse, rebuild offline, identical coverage:

```bash
rm data/ptb.duckdb && uv run ptb rebuild && uv run ptb coverage
```

The grid must be identical, with no network access during `rebuild`.

## Deliberate deviations from the spec

**FPL ingest always fetches, only vaastav is incremental.** The spec described the
element sweep as incremental "within a gameweek". In practice the FPL API is
mutable current-season data whose per-gameweek history grows each week, so
skipping already-archived players would silently miss new gameweeks. FPL therefore
always fetches a fresh timestamped snapshot (the archive is append-only, and
`rebuild` loads the latest per key); vaastav, whose CSVs are static history, is the
incremental source. `--refetch` is accepted by both and ignored by FPL.

## What comes next

- **Spec 3b:** DraftKings forward odds and FotMob season stat-tables -- the last two
  sources in `sacked-in-the-morning`, completing the port.
- **The conformed layer and player identity** (a later spec): FPL, with element ids,
  names, teams and positions, becomes the strongest anchor for `map_player_source`,
  which Understat and ASA player data can then attach to.
