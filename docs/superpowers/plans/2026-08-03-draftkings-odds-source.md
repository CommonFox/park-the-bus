# DraftKings Odds Source — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port the DraftKings forward-odds source (upcoming Premier League 1X2 + Over/Under 2.5) into park-the-bus, proven by resolving a DraftKings event onto the same `dim_match` as its FPL fixture.

**Architecture:** A `Source` fetches the two market subcategories, archives them verbatim in one envelope (`ptb ingest draftkings`), and a loader replays them into a source-faithful `src_draftkings_odds` table (`ptb rebuild`). `resolve_draftkings` maps events into `dim_match` (`E0`). DraftKings is fragile: a `Blocked` from its fingerprint/geo gate is caught and returns zero payloads rather than aborting. Snapshots are append-only (odds are live). De-vigging into probabilities is not done here.

**Tech Stack:** Python 3.12+, `uv` workspace, DuckDB, `requests`, `pytest`.

**Spec:** `docs/superpowers/specs/2026-08-03-draftkings-odds-source-design.md`

## Global Constraints

- Python `>=3.12`. Unchanged from Plans 1-3.
- Seasons stored as `2024/25`; DraftKings has no season field, so the season is derived from the kickoff date (August 2026 -> `2026/27`).
- `RawArchive`'s public API takes and returns **string keys**, never `pathlib.Path`.
- Fetching and loading never occur in the same command.
- Every loader is idempotent (`INSERT OR REPLACE`).
- Nothing is merged across sources. `src_*` tables stay source-faithful (raw decimal odds; no de-vigging).
- Archived payloads are verbatim, self-contained envelopes.
- `data/` is gitignored.
- DraftKings snapshots are append-only (odds are live); a `Blocked` gate failure returns zero payloads, never aborts.

## Confirmed source facts (probed live 2026-08-03)

- Endpoint: `GET https://sportsbook-nash.draftkings.com/sites/US-CO-SB/api/sportscontent/controldata/league/leagueSubcategory/v1/markets` with query `isBatchable=false`, `templateVars={league},{subcategory}`, `eventsQuery=$filter=leagueId eq '{league}' AND clientMetadata/Subcategories/any(s: s/Id eq '{subcategory}')`, `marketsQuery=$filter=clientMetadata/subCategoryId eq '{subcategory}' AND tags/all(t: t ne 'SportcastBetBuilder')`, `include=Events`, `entity=events`.
- Premier League `40253`; subcategories moneyline `4514`, totals `13171`. Site `US-CO-SB`.
- Akamai gates on the web fingerprint headers (`x-client-*`, `x-pe-*`); without them, 403. `x-client-version`/`x-pe-cv` pinned to `2630.3.1.9`.
- Response keys: `sports, leagues, events, markets, selections`. `events[]`: `{id, name: "Home vs Away", startEventDate, participants:[{name, venueRole}]}`. `markets[]`: `{id, eventId, name}`. `selections[]`: `{marketId, outcomeType, points, displayOdds:{decimal, american, ...}}`. Moneyline `outcomeType` is Home / Tie / Away; totals is Over / Under with `points` the line (2.5 wanted).
- Confirmed 200 with ten upcoming fixtures; e.g. event `34297694` "Arsenal vs Coventry" kickoff `2026-08-21T19:00:00.0000000Z`, moneyline Home `decimal 1.15`, totals Over/Under 2.5 `1.54`/`2.35`.

---

## File Structure

| File | Responsibility |
|---|---|
| `.../ptb/core/sources/draftkings.py` | Fetch moneyline + totals, `Blocked`, `draftkings` source |
| `.../ptb/core/warehouse/schema.sql` | Add `src_draftkings_odds` |
| `.../ptb/core/warehouse/loaders/draftkings.py` | Envelope → `src_draftkings_odds` (raw decimal odds) |
| `.../ptb/core/identity/matches.py` | Add `resolve_draftkings` |
| `.../ptb/core/warehouse/load.py` | Call `resolve_draftkings` in `rebuild()` |
| `.../ptb/core/cli.py` | Add a DraftKings arm to `coverage` |
| `tests/fixtures/draftkings_markets_sample.json` | Golden payload |
| `tests/test_draftkings_source.py`, `test_draftkings_loader.py`, `test_identity_draftkings.py` | Tests |

---

### Task 1: DraftKings source and `ptb ingest draftkings`

**Files:**
- Create: `packages/ptb-core/src/ptb/core/sources/draftkings.py`
- Modify: `packages/ptb-core/src/ptb/core/sources/__init__.py` (import for registration)
- Test: `tests/test_draftkings_source.py`

**Interfaces:**
- Consumes: `RawArchive.write`, `register_source`
- Produces:
  - `Blocked(RuntimeError)`
  - `DraftkingsSource.ingest(archive, captured_at=None, **_) -> list[str]`
  - Envelope: `{"source":"draftkings","endpoint":"markets","league":"premier-league","captured_at","url","fetched_at","data":{"moneyline","totals"}}`

- [ ] **Step 1: Write the failing test**

`tests/test_draftkings_source.py`:

```python
import datetime as dt

from ptb.core.archive import LocalBackend, RawArchive
from ptb.core.sources import draftkings as dk


def _payload(subcategory):
    # a minimal well-formed subcategory response
    return {"events": [{"id": "E1", "name": "Arsenal vs Wolves",
                        "startEventDate": "2026-08-21T19:00:00.0000000Z",
                        "participants": [{"name": "Arsenal", "venueRole": "Home"},
                                         {"name": "Wolves", "venueRole": "Away"}]}],
            "markets": [{"id": "M1", "eventId": "E1"}],
            "selections": [{"marketId": "M1", "outcomeType": "Home",
                            "displayOdds": {"decimal": "1.5"}}],
            "subcategory": subcategory}


def test_ingest_archives_one_combined_snapshot(tmp_path, monkeypatch):
    archive = RawArchive(LocalBackend(tmp_path))
    monkeypatch.setattr(dk, "_fetch_subcategory",
                        lambda s, sub, league, site: _payload(sub))

    keys = dk.DraftkingsSource().ingest(archive, captured_at=dt.datetime(2026, 8, 3, 12, 0, 0))

    assert len(keys) == 1
    assert keys[0].startswith("draftkings/markets/")
    env = archive.read(keys[0])
    assert env["league"] == "premier-league"
    assert env["captured_at"] == "2026-08-03T12:00:00Z"
    assert env["data"]["moneyline"]["subcategory"] == dk.SUBCATEGORY_MONEYLINE
    assert env["data"]["totals"]["subcategory"] == dk.SUBCATEGORY_TOTALS


def test_ingest_returns_empty_when_blocked(tmp_path, monkeypatch):
    archive = RawArchive(LocalBackend(tmp_path))

    def blocked(session, subcategory, league, site):
        raise dk.Blocked("fingerprint gate changed")

    monkeypatch.setattr(dk, "_fetch_subcategory", blocked)

    keys = dk.DraftkingsSource().ingest(archive, captured_at=dt.datetime(2026, 8, 3, 12, 0, 0))
    assert keys == []
    assert archive.keys(source="draftkings") == []


def test_ingest_snapshots_are_append_only(tmp_path, monkeypatch):
    archive = RawArchive(LocalBackend(tmp_path))
    monkeypatch.setattr(dk, "_fetch_subcategory",
                        lambda s, sub, league, site: _payload(sub))

    dk.DraftkingsSource().ingest(archive, captured_at=dt.datetime(2026, 8, 3, 12, 0, 0))
    dk.DraftkingsSource().ingest(archive, captured_at=dt.datetime(2026, 8, 3, 13, 0, 0))
    assert len(archive.keys(source="draftkings")) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_draftkings_source.py -v`
Expected: FAIL — `ImportError: cannot import name 'draftkings'`

- [ ] **Step 3: Write draftkings.py**

`packages/ptb-core/src/ptb/core/sources/draftkings.py`:

```python
"""DraftKings -- forward-looking Premier League fixture odds.

Prices fixtures that have not been played yet, so the FPL fdr solver can rate
the gameweek ahead. Two subcategories make a full price: moneyline (1X2) and
total goals Over/Under 2.5. Both raw payloads are archived verbatim in one
snapshot per run; odds are live, so snapshots are append-only.

The endpoint is gated on the web client's Akamai fingerprint headers, and
x-client-version is pinned to a web build that will eventually stop being
accepted. That failure is routine, not fatal: a Blocked gate returns zero
payloads and the run continues. Keeping this behind the standard Source
interface also means a future swap to another odds book touches nothing else.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import requests

from .. import config
from ..archive import RawArchive
from .registry import register_source

log = logging.getLogger(__name__)

BASE = ("https://sportsbook-nash.draftkings.com/sites/{site}/api/sportscontent"
        "/controldata/league/leagueSubcategory/v1/markets")
SITE = "US-CO-SB"
PREMIER_LEAGUE = "40253"
SUBCATEGORY_MONEYLINE = "4514"
SUBCATEGORY_TOTALS = "13171"

# Akamai gates on these; origin + referer + a browser UA alone returns 403.
HEADERS = {
    "accept": "*/*",
    "accept-language": "en-US,en;q=0.8",
    "origin": "https://sportsbook.draftkings.com",
    "referer": "https://sportsbook.draftkings.com/",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
    "user-agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"),
    "x-client-feature": "leagueSubcategory",
    "x-client-name": "web",
    "x-client-page": "league",
    "x-client-version": "2630.3.1.9",
    "x-client-widget-name": "cms",
    "x-client-widget-version": "1.0.0",
    "x-pe-cn": "web",
    "x-pe-cv": "2630.3.1.9",
    "x-pe-ep": "SB",
    "x-pe-loc": "US-CO",
}


class Blocked(RuntimeError):
    """DraftKings refused the request -- fingerprint gate changed, or geo.

    Expected enough to catch: the run falls back rather than aborting.
    """


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    return session


def _fetch_subcategory(session, subcategory, league=PREMIER_LEAGUE, site=SITE) -> Dict[str, Any]:
    query = {
        "isBatchable": "false",
        "templateVars": "{},{}".format(league, subcategory),
        "eventsQuery": ("$filter=leagueId eq '{}' AND "
                        "clientMetadata/Subcategories/any(s: s/Id eq '{}')").format(league, subcategory),
        "marketsQuery": ("$filter=clientMetadata/subCategoryId eq '{}' "
                         "AND tags/all(t: t ne 'SportcastBetBuilder')").format(subcategory),
        "include": "Events",
        "entity": "events",
    }
    url = BASE.format(site=site) + "?" + urlencode(query)
    try:
        resp = session.get(url, timeout=config.REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        raise Blocked("draftkings request failed: {}".format(exc))
    if resp.status_code >= 400:
        raise Blocked("draftkings returned {} for subcategory {}".format(resp.status_code, subcategory))
    try:
        return resp.json()
    except ValueError as exc:
        raise Blocked("draftkings returned non-JSON: {}".format(exc))


@register_source
class DraftkingsSource:
    name = "draftkings"

    def ingest(
        self,
        archive: RawArchive,
        captured_at: Optional[dt.datetime] = None,
        **_ignored,
    ) -> List[str]:
        captured_at = captured_at or dt.datetime.utcnow().replace(microsecond=0)
        session = _session()
        try:
            moneyline = _fetch_subcategory(session, SUBCATEGORY_MONEYLINE)
            totals = _fetch_subcategory(session, SUBCATEGORY_TOTALS)
        except Blocked as exc:
            log.warning("draftkings blocked, skipping: %s", exc)
            return []

        envelope = {
            "source": self.name, "endpoint": "markets", "league": "premier-league",
            "captured_at": captured_at.isoformat() + "Z",
            "url": BASE.format(site=SITE),
            "fetched_at": captured_at.isoformat() + "Z",
            "data": {"moneyline": moneyline, "totals": totals},
        }
        key = archive.write(self.name, "markets", envelope, captured_at=captured_at)
        log.info("archived draftkings snapshot (%d events)",
                 len(moneyline.get("events") or []))
        return [key]
```

- [ ] **Step 4: Register the module**

Append to `packages/ptb-core/src/ptb/core/sources/__init__.py`:

```python
from . import draftkings  # noqa: F401,E402  -- imported for registration side effect
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_draftkings_source.py -v`
Expected: 3 passed

- [ ] **Step 6: Verify against the live API**

DraftKings is fragile, so a real check is worth it:

```bash
uv run python -c "
from ptb.core.sources import draftkings as dk
s = dk._session()
ml = dk._fetch_subcategory(s, dk.SUBCATEGORY_MONEYLINE)
print('events:', len(ml.get('events') or []))
print('first:', (ml.get('events') or [{}])[0].get('name'))
"
```

Expected: a handful of upcoming fixtures and a "Home vs Away" name. If it prints a `Blocked` traceback instead, the fingerprint gate has changed — record that and stop; the port still lands, but ingest will return empty until the headers are refreshed.

- [ ] **Step 7: Commit**

```bash
git add packages/ptb-core/src/ptb/core/sources/ tests/test_draftkings_source.py
git commit -m "Ingest DraftKings forward odds into the archive"
```

---

### Task 2: DraftKings loader

**Files:**
- Modify: `packages/ptb-core/src/ptb/core/warehouse/schema.sql` (`src_draftkings_odds`)
- Create: `packages/ptb-core/src/ptb/core/warehouse/loaders/draftkings.py`
- Modify: `packages/ptb-core/src/ptb/core/warehouse/loaders/__init__.py`
- Create: `tests/fixtures/draftkings_markets_sample.json`
- Test: `tests/test_draftkings_loader.py`

**Interfaces:**
- Consumes: `LOADERS`, the envelope from Task 1
- Produces: `load_draftkings(con, payload, archive_key) -> int`, `season_from_kickoff(dt) -> str`

- [ ] **Step 1: Extend the schema**

Append to `packages/ptb-core/src/ptb/core/warehouse/schema.sql`:

```sql
-- ------------------------------------------------------------- draftkings

CREATE TABLE IF NOT EXISTS src_draftkings_odds (
    dk_event_id     TEXT NOT NULL,
    captured_at     TIMESTAMP NOT NULL,
    season          TEXT,
    kickoff_utc     TIMESTAMP,
    home_team       TEXT NOT NULL,
    away_team       TEXT NOT NULL,
    moneyline_home  DOUBLE,
    moneyline_draw  DOUBLE,
    moneyline_away  DOUBLE,
    over_2_5        DOUBLE,
    under_2_5       DOUBLE,
    archive_key     TEXT NOT NULL,
    PRIMARY KEY (dk_event_id, captured_at)
);
```

- [ ] **Step 2: Create the golden fixture**

`tests/fixtures/draftkings_markets_sample.json` (real shape; one fully-priced event and one missing its totals, which must be skipped):

```json
{
  "source": "draftkings", "endpoint": "markets", "league": "premier-league",
  "captured_at": "2026-08-03T12:00:00Z",
  "url": "https://sportsbook-nash.draftkings.com/sites/US-CO-SB/api/sportscontent/controldata/league/leagueSubcategory/v1/markets",
  "fetched_at": "2026-08-03T12:00:00Z",
  "data": {
    "moneyline": {
      "events": [
        {"id": "34297694", "name": "Arsenal vs Wolves",
         "startEventDate": "2026-08-21T19:00:00.0000000Z",
         "participants": [{"name": "Arsenal", "venueRole": "Home"},
                          {"name": "Wolves", "venueRole": "Away"}]},
        {"id": "34297695", "name": "Everton vs Brighton",
         "startEventDate": "2026-08-22T14:00:00.0000000Z",
         "participants": [{"name": "Everton", "venueRole": "Home"},
                          {"name": "Brighton", "venueRole": "Away"}]}
      ],
      "markets": [{"id": "M1", "eventId": "34297694"}, {"id": "M2", "eventId": "34297695"}],
      "selections": [
        {"marketId": "M1", "outcomeType": "Home", "displayOdds": {"decimal": "1.15"}},
        {"marketId": "M1", "outcomeType": "Tie", "displayOdds": {"decimal": "8.00"}},
        {"marketId": "M1", "outcomeType": "Away", "displayOdds": {"decimal": "13.00"}},
        {"marketId": "M2", "outcomeType": "Home", "displayOdds": {"decimal": "2.10"}},
        {"marketId": "M2", "outcomeType": "Tie", "displayOdds": {"decimal": "3.40"}},
        {"marketId": "M2", "outcomeType": "Away", "displayOdds": {"decimal": "3.30"}}
      ]
    },
    "totals": {
      "events": [],
      "markets": [{"id": "T1", "eventId": "34297694"}],
      "selections": [
        {"marketId": "T1", "outcomeType": "Over", "points": 2.5, "displayOdds": {"decimal": "1.54"}},
        {"marketId": "T1", "outcomeType": "Under", "points": 2.5, "displayOdds": {"decimal": "2.35"}},
        {"marketId": "T1", "outcomeType": "Over", "points": 3.5, "displayOdds": {"decimal": "3.00"}}
      ]
    }
  }
}
```

- [ ] **Step 3: Write the failing test**

`tests/test_draftkings_loader.py`:

```python
import datetime as dt
import json
from pathlib import Path

import pytest

from ptb.core.warehouse import db
from ptb.core.warehouse.loaders import draftkings as loader

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def con(tmp_path):
    connection = db.connect(tmp_path / "test.duckdb")
    yield connection
    connection.close()


def _load(con):
    payload = json.loads((FIX / "draftkings_markets_sample.json").read_text(encoding="utf-8"))
    return loader.load_draftkings(con, payload, "draftkings/markets/k.json.gz")


def test_season_from_kickoff():
    assert loader.season_from_kickoff(dt.datetime(2026, 8, 21, 19, 0)) == "2026/27"
    assert loader.season_from_kickoff(dt.datetime(2025, 5, 1, 14, 0)) == "2024/25"


def test_draftkings_is_registered():
    from ptb.core.warehouse.load import LOADERS
    assert "draftkings" in LOADERS


def test_loads_only_fully_priced_events(con):
    # Arsenal has 1X2 + O/U 2.5; Everton has 1X2 but no totals -> skipped.
    assert _load(con) == 1
    assert con.execute("SELECT count(*) FROM src_draftkings_odds").fetchone()[0] == 1
    assert con.execute(
        "SELECT count(*) FROM src_draftkings_odds WHERE dk_event_id = '34297695'"
    ).fetchone()[0] == 0


def test_maps_odds_and_metadata(con):
    _load(con)
    row = con.execute(
        "SELECT captured_at, season, kickoff_utc, home_team, away_team, "
        "       moneyline_home, moneyline_draw, moneyline_away, over_2_5, under_2_5 "
        "FROM src_draftkings_odds WHERE dk_event_id = '34297694'"
    ).fetchone()
    assert row[0] == dt.datetime(2026, 8, 3, 12, 0)
    assert row[1] == "2026/27"
    assert row[2] == dt.datetime(2026, 8, 21, 19, 0)
    assert (row[3], row[4]) == ("Arsenal", "Wolves")
    assert row[5] == pytest.approx(1.15)
    assert row[6] == pytest.approx(8.00)
    assert row[7] == pytest.approx(13.00)
    assert row[8] == pytest.approx(1.54)   # over 2.5 only, not the 3.5 line
    assert row[9] == pytest.approx(2.35)


def test_loader_is_idempotent(con):
    _load(con)
    _load(con)
    assert con.execute("SELECT count(*) FROM src_draftkings_odds").fetchone()[0] == 1
```

- [ ] **Step 4: Run test to verify it fails**

Run: `uv run pytest tests/test_draftkings_loader.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ptb.core.warehouse.loaders.draftkings'`

- [ ] **Step 5: Write the loader**

`packages/ptb-core/src/ptb/core/warehouse/loaders/draftkings.py`:

```python
"""DraftKings snapshot -> src_draftkings_odds (raw decimal odds).

One envelope holds a moneyline and a totals payload. Rows are joined by event
id: a row is written only when an event has the full 1X2 and the Over/Under 2.5
line. Odds are stored raw; de-vigging into probabilities is the fdr solver's job.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, Optional

import duckdb

from ..load import LOADERS

_TOTALS_LINE = 2.5
_COLUMNS = [
    "dk_event_id", "captured_at", "season", "kickoff_utc", "home_team", "away_team",
    "moneyline_home", "moneyline_draw", "moneyline_away", "over_2_5", "under_2_5",
    "archive_key",
]


def _f(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ts(value: Any) -> Optional[dt.datetime]:
    if not value:
        return None
    text = str(value).replace("Z", "").strip()
    # DraftKings uses 7-digit fractional seconds; trim to microseconds if present.
    if "." in text:
        head, frac = text.split(".", 1)
        text = "{}.{}".format(head, frac[:6])
    try:
        return dt.datetime.fromisoformat(text)
    except ValueError:
        try:
            return dt.datetime.fromisoformat(text.split(".")[0])
        except ValueError:
            return None


def season_from_kickoff(kickoff: dt.datetime) -> str:
    """A Premier League season runs Aug-May; an Aug 2026 kickoff is '2026/27'."""
    start = kickoff.year if kickoff.month >= 7 else kickoff.year - 1
    return "{}/{:02d}".format(start, (start + 1) % 100)


def _odds_by_event(payload: dict, totals: bool) -> Dict[str, Dict[str, float]]:
    market_event = {m["id"]: m.get("eventId") for m in payload.get("markets") or []}
    out: Dict[str, Dict[str, float]] = {}
    for sel in payload.get("selections") or []:
        if totals and sel.get("points") != _TOTALS_LINE:
            continue
        event_id = market_event.get(sel.get("marketId"))
        outcome = sel.get("outcomeType")
        price = _f((sel.get("displayOdds") or {}).get("decimal"))
        if event_id and outcome and price and price > 0:
            out.setdefault(event_id, {})[outcome] = price
    return out


def _teams(event: dict) -> tuple:
    participants = event.get("participants") or []
    home = next((p.get("name") for p in participants if p.get("venueRole") == "Home"), None)
    away = next((p.get("name") for p in participants if p.get("venueRole") == "Away"), None)
    if not home or not away:
        name = event.get("name") or ""
        if " vs " in name:
            home, away = [part.strip() for part in name.split(" vs ", 1)]
    return home, away


def load_draftkings(con: duckdb.DuckDBPyConnection, payload: dict, archive_key: str) -> int:
    captured_at = _ts(payload.get("captured_at"))
    data = payload["data"]
    moneyline, totals = data.get("moneyline") or {}, data.get("totals") or {}

    events = {e["id"]: e for e in moneyline.get("events") or []}
    ml_odds = _odds_by_event(moneyline, totals=False)
    ou_odds = _odds_by_event(totals, totals=True)

    rows = []
    for event_id, event in events.items():
        one = ml_odds.get(event_id) or {}
        over_under = ou_odds.get(event_id) or {}
        if not all(k in one for k in ("Home", "Tie", "Away")):
            continue
        if not all(k in over_under for k in ("Over", "Under")):
            continue
        home, away = _teams(event)
        if not home or not away:
            continue
        kickoff = _ts(event.get("startEventDate"))
        season = season_from_kickoff(kickoff) if kickoff else None
        rows.append([
            event_id, captured_at, season, kickoff, home, away,
            one["Home"], one["Tie"], one["Away"],
            over_under["Over"], over_under["Under"], archive_key,
        ])

    if rows:
        con.executemany(
            "INSERT OR REPLACE INTO src_draftkings_odds ({}) VALUES ({})".format(
                ", ".join(_COLUMNS), ", ".join("?" * len(_COLUMNS))),
            rows)
    return len(rows)


LOADERS["draftkings"] = load_draftkings
```

`packages/ptb-core/src/ptb/core/warehouse/loaders/__init__.py` — append:

```python
from . import draftkings  # noqa: F401  -- registers the draftkings loader
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_draftkings_loader.py -v`
Expected: 5 passed

- [ ] **Step 7: Commit**

```bash
git add packages/ptb-core/src/ptb/core/warehouse/ tests/fixtures/draftkings_markets_sample.json tests/test_draftkings_loader.py
git commit -m "Load DraftKings forward odds into the warehouse"
```

---

### Task 3: DraftKings resolution and coverage

**Files:**
- Modify: `packages/ptb-core/src/ptb/core/identity/matches.py` (`resolve_draftkings`)
- Modify: `packages/ptb-core/src/ptb/core/warehouse/load.py` (call it in `rebuild`)
- Modify: `packages/ptb-core/src/ptb/core/cli.py` (coverage arm)
- Test: `tests/test_identity_draftkings.py`, extend `tests/test_coverage.py`

**Interfaces:**
- Consumes: `resolve_match`, `src_draftkings_odds`
- Produces: `resolve_draftkings(con) -> int`

- [ ] **Step 1: Write the failing test**

`tests/test_identity_draftkings.py`:

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


def _dk(con, event_id, captured_at, home, away, kickoff):
    con.execute(
        "INSERT INTO src_draftkings_odds "
        "(dk_event_id, captured_at, season, kickoff_utc, home_team, away_team, archive_key) "
        "VALUES (?, ?, '2026/27', ?, ?, ?, 'k')",
        [event_id, captured_at, kickoff, home, away])


def test_resolve_draftkings_maps_events(con):
    _dk(con, "E1", dt.datetime(2026, 8, 3, 12, 0), "Arsenal", "Wolves",
        dt.datetime(2026, 8, 21, 19, 0))
    assert matches.resolve_draftkings(con) == 1
    assert con.execute("SELECT count(*) FROM dim_match").fetchone()[0] == 1


def test_resolve_uses_latest_snapshot_only(con):
    # two snapshots of one event must still be one dim_match / one map row
    _dk(con, "E1", dt.datetime(2026, 8, 3, 12, 0), "Arsenal", "Wolves",
        dt.datetime(2026, 8, 21, 19, 0))
    _dk(con, "E1", dt.datetime(2026, 8, 3, 18, 0), "Arsenal", "Wolves",
        dt.datetime(2026, 8, 21, 19, 0))
    matches.resolve_draftkings(con)
    assert con.execute("SELECT count(*) FROM dim_match").fetchone()[0] == 1
    assert con.execute(
        "SELECT count(*) FROM map_match_source WHERE source = 'draftkings'"
    ).fetchone()[0] == 1


def test_draftkings_and_fpl_resolve_to_one_match(con):
    """A DraftKings event and the FPL fixture for the same upcoming match land
    on one dim_match."""
    con.execute(
        "INSERT INTO src_fpl_team (season, team_id, name, archive_key) VALUES "
        "('2026/27', 1, 'Arsenal', 'k'), ('2026/27', 20, 'Wolves', 'k')")
    con.execute(
        "INSERT INTO src_fpl_fixture (season, fixture_id, event, kickoff_time, team_h, "
        "team_a, archive_key) VALUES ('2026/27', 1, 1, TIMESTAMP '2026-08-21 19:00:00', 1, 20, 'k')")
    matches.resolve_fpl(con)

    _dk(con, "E1", dt.datetime(2026, 8, 3, 12, 0), "Arsenal", "Wolves",
        dt.datetime(2026, 8, 21, 19, 0))
    matches.resolve_draftkings(con)

    assert con.execute("SELECT count(*) FROM dim_match").fetchone()[0] == 1
    assert con.execute("SELECT count(DISTINCT source) FROM map_match_source").fetchone()[0] == 2


def test_resolve_draftkings_is_idempotent(con):
    _dk(con, "E1", dt.datetime(2026, 8, 3, 12, 0), "Arsenal", "Wolves",
        dt.datetime(2026, 8, 21, 19, 0))
    matches.resolve_draftkings(con)
    matches.resolve_draftkings(con)
    assert con.execute("SELECT count(*) FROM dim_match").fetchone()[0] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_identity_draftkings.py -v`
Expected: FAIL — `AttributeError: module 'ptb.core.identity.matches' has no attribute 'resolve_draftkings'`

- [ ] **Step 3: Write `resolve_draftkings`**

Append to `packages/ptb-core/src/ptb/core/identity/matches.py`:

```python
def resolve_draftkings(con: duckdb.DuckDBPyConnection) -> int:
    """Resolve DraftKings events into dim_match. Idempotent.

    Odds are snapshotted, so only the latest snapshot per event is resolved.
    DraftKings events are upcoming Premier League matches, so they resolve
    against the FPL forward fixtures (and, once played, football-data) already in
    dim_match via the +/-36h window.
    """
    rows = con.execute(
        "SELECT dk_event_id, season, kickoff_utc, home_team, away_team FROM ("
        "  SELECT *, row_number() OVER "
        "    (PARTITION BY dk_event_id ORDER BY captured_at DESC) AS rn "
        "  FROM src_draftkings_odds WHERE kickoff_utc IS NOT NULL AND season IS NOT NULL"
        ") WHERE rn = 1 ORDER BY kickoff_utc, dk_event_id"
    ).fetchall()

    resolved = 0
    for event_id, season, kickoff, home, away in rows:
        if resolve_match(
            con, source="draftkings", source_match_id=str(event_id),
            competition="E0", season=season, kickoff=kickoff,
            home_team=home, away_team=away,
        ) is not None:
            resolved += 1
    return resolved
```

- [ ] **Step 4: Wire it into `rebuild`**

In `packages/ptb-core/src/ptb/core/warehouse/load.py`, extend the identity block so it ends:

```python
    if written.get("fpl"):
        resolved = identity_matches.resolve_fpl(con)
        log.info("resolved %d fpl fixtures into dim_match", resolved)

    if written.get("draftkings"):
        resolved = identity_matches.resolve_draftkings(con)
        log.info("resolved %d draftkings events into dim_match", resolved)

    return written
```

- [ ] **Step 5: Add the coverage arm**

In `packages/ptb-core/src/ptb/core/cli.py`, in `_cmd_coverage`, add before the final `ORDER BY 1, 2, 3` (a snapshot source, so count distinct events, not rows):

```python
            "UNION ALL "
            "SELECT 'draftkings', 'E0', season, count(DISTINCT dk_event_id) "
            "  FROM src_draftkings_odds WHERE season IS NOT NULL GROUP BY 1, 2, 3 "
```

- [ ] **Step 6: Extend the coverage test**

In `tests/test_coverage.py`, add to the `warehouse` fixture body before `con.close()`:

```python
    con.execute(
        "INSERT INTO src_draftkings_odds "
        "(dk_event_id, captured_at, season, kickoff_utc, home_team, away_team, archive_key) "
        "VALUES ('E1', TIMESTAMP '2026-08-03 12:00:00', '2026/27', "
        "        TIMESTAMP '2026-08-21 19:00:00', 'Arsenal', 'Wolves', 'k')"
    )
```

and add a test:

```python
def test_coverage_includes_draftkings(warehouse):
    out = io.StringIO()
    with redirect_stdout(out):
        rc = _cmd_coverage(_Args())
    assert rc == 0
    assert "draftkings" in out.getvalue()
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/test_identity_draftkings.py tests/test_coverage.py -v`
Expected: all pass

- [ ] **Step 8: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass

- [ ] **Step 9: Commit**

```bash
git add packages/ptb-core/src/ptb/core/ tests/test_identity_draftkings.py tests/test_coverage.py
git commit -m "Resolve DraftKings events into dim_match and report them in coverage"
```

---

## Verification

After Task 3, this sequence should work end to end:

```bash
uv run pytest

# DraftKings forward odds (2 requests) + FPL fixtures for the same upcoming season
uv run ptb ingest draftkings
uv run ptb ingest fpl
uv run ptb rebuild
uv run ptb coverage
```

Then confirm the cross-source pairing -- an upcoming match carrying DraftKings odds and its FPL fixture:

```bash
uv run python -c "
from ptb.core.warehouse import db
con = db.connect(read_only=True)
n = con.execute('''
  SELECT count(*) FROM (
    SELECT match_id FROM map_match_source WHERE source IN ('draftkings','fpl')
    GROUP BY match_id HAVING count(DISTINCT source) = 2)
''').fetchone()[0]
print('matches carrying both draftkings and fpl:', n)
"
```

Expected: a positive count of upcoming fixtures resolving across both sources (both are forward-looking, so they overlap even pre-season). If `ptb ingest draftkings` archived nothing, the fingerprint gate is blocking — the pipeline is still correct, but there is no live data to resolve; note it and move on.

Then disposability:

```bash
rm data/ptb.duckdb && uv run ptb rebuild && uv run ptb coverage
```

The grid must be identical, with no network access during `rebuild`.

## Notes

**Fragility is expected.** If the live check in Task 1 Step 6 or the verification
returns `Blocked`, that is DraftKings' fingerprint gate, not a bug in this code.
The source returns empty and the run continues. Refreshing `x-client-version` /
`x-pe-cv` to a current web build restores it, or the source is swapped for
another book -- which is why it is isolated behind the `Source` interface.

**No per-source CLI odds flag.** `ptb ingest draftkings` takes no options; it
always pulls the current Premier League board.

## What comes next

- The **football-data odds enrichment** (widen Plan 1's odds columns) is the only
  remaining odds work, and it is optional -- DraftKings covers forward odds,
  football-data covers settled results.
- The **conformed layer**: a `v_match_odds` view that de-vigs DraftKings into
  probabilities and applies source precedence, which the FPL `fdr` solver reads.
- The **FPL modelling port** itself, now that every source it reads is in the
  warehouse.
