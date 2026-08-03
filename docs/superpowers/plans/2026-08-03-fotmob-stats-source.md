# FotMob Stats Source — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port the FotMob season stat-tables source (player and team leaderboards for the Premier League and Championship) into park-the-bus, so every source the FPL app reads lives in the universal warehouse.

**Architecture:** A `Source` fetches verbatim `leagueseasondeepstats` payloads into the archive (`ptb ingest fotmob`), and a loader replays them into source-faithful `src_fotmob_player_stat` / `src_fotmob_team_stat` tables (`ptb rebuild`). FotMob stat-tables carry no fixtures, so there is no match resolution and FotMob does not appear in `ptb coverage`. Ingest is incremental by `(league, season, stat, type)`; `--refetch` re-pulls the live season.

**Tech Stack:** Python 3.12+, `uv` workspace, DuckDB, `requests`, `pytest`.

**Spec:** `docs/superpowers/specs/2026-08-03-fotmob-stats-source-design.md`

## Global Constraints

- Python `>=3.12`. Unchanged from Plans 1-3.
- Seasons stored as `2024/25`. FotMob's `2024/2025` name converts at fetch time; the `season` query parameter is a numeric season **id** discovered from each response's `seasons` array.
- `RawArchive`'s public API takes and returns **string keys**, never `pathlib.Path`.
- Fetching and loading never occur in the same command.
- Every loader is idempotent (`INSERT OR REPLACE`).
- Nothing is merged across sources. `src_*` tables stay source-faithful.
- Archived payloads are verbatim, self-contained envelopes.
- `data/` is gitignored.
- Ingest is incremental by `(league, season, stat, type)`; `--refetch` re-pulls.

## Confirmed source facts (probed live 2026-08-03)

- Endpoint: `GET https://www.fotmob.com/api/data/leagueseasondeepstats?id={leagueId}&season={seasonId}&stat={stat}&type={players|teams}`. A plain session works (the browser's `x-mas` header is not enforced).
- Response keys: `statsData`, `statsList`, `seasons`, `leagueDetails`, `type`, `currentSeasonId`.
- `seasons[]`: `{id, name, leagueName, leagueId}` — e.g. `{id: 23685, name: "2024/2025", leagueId: 47}`. Premier League (47) has 11 seasons, `2016/2017`..`2026/2027`.
- `statsList[]` (type-specific, season-aware): `{name, title, category, localizedTitleId, localizedCategoryId}` — e.g. `{name: "expected_goals", title: "Expected goals (xG)", category: "Attacking"}`. ~37 player stats and ~29 team stats for a recent PL season.
- `statsData[]` row: `{id, teamId, name, position, statValue: {name, value, format, fractions}, substatValue: {value, format, fractions}, rank, type}`. For teams, `id == teamId` and `position` is null. The stat value is `statValue.value`; the paired secondary is `substatValue.value`.
- Asking for a stat a season does not offer returns 200 with empty `statsData`.
- Leagues: Premier League `premier-league` = 47, Championship `championship` = 48.
- Probe stats present in every season: `goals` (players), `rating_team` (teams).

---

## File Structure

| File | Responsibility |
|---|---|
| `.../ptb/core/sources/fotmob.py` | FotMob fetch (discover seasons + statsList, sweep stats), `fotmob` source |
| `.../ptb/core/warehouse/schema.sql` | Add `src_fotmob_player_stat`, `src_fotmob_team_stat` |
| `.../ptb/core/warehouse/loaders/fotmob.py` | FotMob envelopes → the two stat tables, routed on type |
| `tests/fixtures/fotmob_players_xg_sample.json`, `fotmob_teams_xg_sample.json` | Golden payloads |
| `tests/test_fotmob_source.py`, `test_fotmob_loader.py` | Tests |

---

### Task 1: FotMob source and `ptb ingest fotmob`

**Files:**
- Create: `packages/ptb-core/src/ptb/core/sources/fotmob.py`
- Modify: `packages/ptb-core/src/ptb/core/sources/__init__.py` (import for registration)
- Test: `tests/test_fotmob_source.py`

**Interfaces:**
- Consumes: `RawArchive.write`, `RawArchive.keys`, `register_source`
- Produces:
  - `LEAGUES: dict[str, int]` = `{"premier-league": 47, "championship": 48}`
  - `season_label("2024/2025") -> "2024/25"`
  - `archived_stats(archive) -> set[str]` of `"{league}/{type}/{season}__{stat}"`
  - `FotmobSource.ingest(archive, leagues=..., seasons=..., refetch=False, captured_at=None) -> list[str]`
  - Envelope: `{"source":"fotmob","endpoint":"{league}/{type}","league","league_id","season","season_id","stat","stat_type","url","fetched_at","data"}`

- [ ] **Step 1: Write the failing test**

`tests/test_fotmob_source.py`:

```python
import datetime as dt

import pytest

from ptb.core.archive import LocalBackend, RawArchive
from ptb.core.sources import fotmob as fm


def test_season_label_shortens_fotmob_name():
    assert fm.season_label("2024/2025") == "2024/25"
    assert fm.season_label("2016/2017") == "2016/17"


def test_leagues_are_premier_league_and_championship():
    assert fm.LEAGUES == {"premier-league": 47, "championship": 48}


# A canned API response for one (season, stat, type).
def _response(stat, stat_type, names):
    return {
        "seasons": [{"id": 23685, "name": "2024/2025", "leagueId": 47},
                    {"id": 27110, "name": "2025/2026", "leagueId": 47}],
        "statsList": [{"name": n, "title": n.title(), "category": "Attacking"} for n in names],
        "leagueDetails": {"id": 47, "name": "Premier League"},
        "statsData": [{"id": 1, "teamId": 2, "name": "Player", "position": 83,
                       "statValue": {"name": stat, "value": 1.0}, "substatValue": {"value": 2},
                       "rank": 1, "type": stat_type}],
        "type": stat_type,
    }


def _patch(monkeypatch, calls):
    monkeypatch.setattr(fm, "_seasons",
                        lambda s, lid: [{"id": 23685, "name": "2024/2025", "leagueId": lid}])
    monkeypatch.setattr(fm, "_stats_list",
                        lambda s, lid, sid, t: ["goals"] if t == "players" else ["rating_team"])

    def fake_fetch(session, league_id, season_id, stat, stat_type):
        calls.append((league_id, season_id, stat, stat_type))
        return _response(stat, stat_type, ["goals"] if stat_type == "players" else ["rating_team"])

    monkeypatch.setattr(fm, "_fetch_stat", fake_fetch)


def test_ingest_sweeps_each_stat_and_type(tmp_path, monkeypatch):
    archive = RawArchive(LocalBackend(tmp_path))
    calls = []
    _patch(monkeypatch, calls)

    keys = fm.FotmobSource().ingest(
        archive, leagues=["premier-league"], seasons=["2024/25"],
        captured_at=dt.datetime(2026, 8, 3, 12, 0, 0))

    assert any(k.startswith("fotmob/premier-league/players/2024-25__goals__") for k in keys)
    assert any(k.startswith("fotmob/premier-league/teams/2024-25__rating_team__") for k in keys)

    env = archive.read(next(k for k in keys if "/players/2024-25__goals__" in k))
    assert env["league_id"] == 47
    assert env["season"] == "2024/25"
    assert env["season_id"] == 23685
    assert env["stat"] == "goals"
    assert env["stat_type"] == "players"
    assert env["data"]["statsData"][0]["name"] == "Player"


def test_ingest_is_incremental(tmp_path, monkeypatch):
    archive = RawArchive(LocalBackend(tmp_path))
    calls = []
    _patch(monkeypatch, calls)

    fm.FotmobSource().ingest(archive, leagues=["premier-league"], seasons=["2024/25"],
                             captured_at=dt.datetime(2026, 8, 3, 12, 0, 0))
    first = len(calls)
    fm.FotmobSource().ingest(archive, leagues=["premier-league"], seasons=["2024/25"],
                             captured_at=dt.datetime(2026, 8, 3, 13, 0, 0))
    assert len(calls) == first  # everything already archived


def test_refetch_repulls(tmp_path, monkeypatch):
    archive = RawArchive(LocalBackend(tmp_path))
    calls = []
    _patch(monkeypatch, calls)

    fm.FotmobSource().ingest(archive, leagues=["premier-league"], seasons=["2024/25"],
                             captured_at=dt.datetime(2026, 8, 3, 12, 0, 0))
    n = len(calls)
    fm.FotmobSource().ingest(archive, leagues=["premier-league"], seasons=["2024/25"],
                             refetch=True, captured_at=dt.datetime(2026, 8, 3, 13, 0, 0))
    assert len(calls) > n


def test_ingest_rejects_unknown_league(tmp_path):
    archive = RawArchive(LocalBackend(tmp_path))
    with pytest.raises(ValueError, match="la-liga"):
        fm.FotmobSource().ingest(archive, leagues=["la-liga"], seasons=["2024/25"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_fotmob_source.py -v`
Expected: FAIL — `ImportError: cannot import name 'fotmob'`

- [ ] **Step 3: Write fotmob.py**

`packages/ptb-core/src/ptb/core/sources/fotmob.py`:

```python
"""FotMob -- season stat-table leaderboards for the Premier League and Championship.

The endpoint `leagueseasondeepstats` returns one stat's leaderboard for one
season and type (players or teams). A full sweep is len(statsList) requests per
season per type. statsList is season-aware, so the sweep is driven off each
season's own statsList, discovered with a probe request. The season query
parameter is a numeric season id, itself discovered from the `seasons` array.

Ingest is incremental by (league, season, stat, type): a leaderboard already in
the archive is skipped unless `refetch`, so completed seasons are pulled once and
only the live season is refreshed.
"""
from __future__ import annotations

import datetime as dt
import logging
import random
import time
from typing import Any, Dict, List, Optional, Sequence, Set

import requests

from .. import config
from ..archive import RawArchive
from .registry import register_source

log = logging.getLogger(__name__)

BASE = "https://www.fotmob.com/api/data/leagueseasondeepstats"
LEAGUES = {"premier-league": 47, "championship": 48}
STAT_TYPES = ("players", "teams")
PROBE_STAT = {"players": "goals", "teams": "rating_team"}
_POLITENESS_SECONDS = 0.5


def season_label(fotmob_name: str) -> str:
    """'2024/2025' -> '2024/25'."""
    start, end = fotmob_name.split("/")
    return "{}/{}".format(start, end[2:])


def _to_label(season) -> str:
    """Normalise a requested season to the warehouse label '2024/25'.

    Accepts an int start year (2024, as the CLI produces via parse_season), the
    slash form '2024/25', or the dash form '2024-25'."""
    if isinstance(season, int):
        return "{}/{:02d}".format(season, (season + 1) % 100)
    text = str(season)
    if "-" in text:
        start = int(text.split("-")[0])
        return "{}/{:02d}".format(start, (start + 1) % 100)
    return text


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": config.USER_AGENT,
        "Accept": "application/json",
    })
    return session


def _get(session: requests.Session, params: Dict[str, Any], retries: int = 3) -> Dict[str, Any]:
    last: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            resp = session.get(BASE, params=params, timeout=config.REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as exc:
            last = exc
            if attempt < retries:
                time.sleep((2 ** attempt) + random.random())
    raise RuntimeError("failed to fetch fotmob {}: {}".format(params, last))


def _fetch_stat(session, league_id, season_id, stat, stat_type) -> Dict[str, Any]:
    return _get(session, {"id": league_id, "season": season_id, "stat": stat, "type": stat_type})


def _seasons(session, league_id) -> List[Dict[str, Any]]:
    """Discover the league's seasons (id + name) via a probe request.

    With no season parameter the endpoint still returns the league's full
    `seasons` array (confirmed live), which is the id-to-name map the sweep needs.
    """
    resp = _get(session, {"id": league_id, "stat": PROBE_STAT["players"], "type": "players"})
    return resp.get("seasons", [])


def _stats_list(session, league_id, season_id, stat_type) -> List[str]:
    """Discover a season's available stat names for a type."""
    resp = _fetch_stat(session, league_id, season_id, PROBE_STAT[stat_type], stat_type)
    return [s["name"] for s in resp.get("statsList", []) if s.get("name")]


def archived_stats(archive: RawArchive) -> Set[str]:
    """Set of '{league}/{type}/{season}__{stat}' already in the archive."""
    done: Set[str] = set()
    for key in archive.keys(source="fotmob"):
        body = key[len("fotmob/"):]                # {league}/{type}/{label}__{stamp}.json.gz
        endpoint, _, name = body.rpartition("/")   # endpoint = {league}/{type}
        label = name.rsplit("__", 1)[0]            # {season}__{stat}
        done.add("{}/{}".format(endpoint, label))
    return done


@register_source
class FotmobSource:
    name = "fotmob"

    def ingest(
        self,
        archive: RawArchive,
        leagues: Optional[Sequence[str]] = None,
        seasons: Optional[Sequence[str]] = None,
        refetch: bool = False,
        captured_at: Optional[dt.datetime] = None,
        **_ignored,
    ) -> List[str]:
        leagues = list(leagues or LEAGUES)
        captured_at = captured_at or dt.datetime.utcnow().replace(microsecond=0)
        # Filter of season labels ('2024/25'); accepts int/slash/dash forms so the
        # CLI's --season (parsed to an int start year) works too.
        wanted = {_to_label(s) for s in seasons} if seasons else None

        unknown = [lg for lg in leagues if lg not in LEAGUES]
        if unknown:
            raise ValueError("unknown league(s): {}".format(", ".join(unknown)))

        session = _session()
        done = set() if refetch else archived_stats(archive)
        written: List[str] = []

        for slug in leagues:
            league_id = LEAGUES[slug]
            for season in _seasons(session, league_id):
                label = season_label(season["name"])
                if wanted is not None and label not in wanted:
                    continue
                season_id = season["id"]
                dash = label.replace("/", "-")
                for stat_type in STAT_TYPES:
                    for stat in _stats_list(session, league_id, season_id, stat_type):
                        marker = "{}/{}/{}__{}".format(slug, stat_type, dash, stat)
                        if marker in done:
                            continue
                        data = _fetch_stat(session, league_id, season_id, stat, stat_type)
                        envelope = {
                            "source": self.name, "endpoint": "{}/{}".format(slug, stat_type),
                            "league": slug, "league_id": league_id, "season": label,
                            "season_id": season_id, "stat": stat, "stat_type": stat_type,
                            "url": BASE, "fetched_at": captured_at.isoformat() + "Z",
                            "data": data,
                        }
                        written.append(archive.write(
                            self.name, "{}/{}".format(slug, stat_type), envelope,
                            label="{}__{}".format(dash, stat), captured_at=captured_at))
                        done.add(marker)
                        time.sleep(_POLITENESS_SECONDS)
                log.info("archived fotmob %s %s", slug, label)

        return written
```

Note: `_seasons` omits the season parameter entirely; FotMob still returns the league's full `seasons` array (confirmed live), which is the id-to-name map the sweep drives off.

- [ ] **Step 4: Register the module**

Append to `packages/ptb-core/src/ptb/core/sources/__init__.py`:

```python
from . import fotmob  # noqa: F401,E402  -- imported for registration side effect
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_fotmob_source.py -v`
Expected: 6 passed

- [ ] **Step 6: Verify against the live API**

FotMob is scraped and fragile, so a real check is worth it here:

```bash
uv run python -c "
from ptb.core.sources import fotmob as fm
s = fm._session()
seasons = fm._seasons(s, 47)
print('PL seasons:', [x['name'] for x in seasons][:3], '...')
sl = fm._stats_list(s, 47, seasons[2]['id'], 'players')
print('player stats for', seasons[2]['name'], ':', len(sl))
"
```

Expected: a list of PL seasons and a stat count around 37.

- [ ] **Step 7: Commit**

```bash
git add packages/ptb-core/src/ptb/core/sources/ tests/test_fotmob_source.py
git commit -m "Ingest FotMob season stat-tables into the archive"
```

---

### Task 2: FotMob loader

**Files:**
- Modify: `packages/ptb-core/src/ptb/core/warehouse/schema.sql` (`src_fotmob_player_stat`, `src_fotmob_team_stat`)
- Create: `packages/ptb-core/src/ptb/core/warehouse/loaders/fotmob.py`
- Modify: `packages/ptb-core/src/ptb/core/warehouse/loaders/__init__.py`
- Create: `tests/fixtures/fotmob_players_xg_sample.json`, `tests/fixtures/fotmob_teams_xg_sample.json`
- Test: `tests/test_fotmob_loader.py`

**Interfaces:**
- Consumes: `LOADERS`, envelopes from Task 1
- Produces: `load_fotmob(con, payload, archive_key) -> int` routed on `payload["stat_type"]`, registered `LOADERS["fotmob"]`

- [ ] **Step 1: Extend the schema**

Append to `packages/ptb-core/src/ptb/core/warehouse/schema.sql`:

```sql
-- ----------------------------------------------------------------- fotmob

CREATE TABLE IF NOT EXISTS src_fotmob_player_stat (
    league_id        BIGINT NOT NULL,
    league_name      TEXT,
    season           TEXT NOT NULL,
    season_id        BIGINT,
    stat_name        TEXT NOT NULL,
    stat_title       TEXT,
    stat_category    TEXT,
    fotmob_player_id BIGINT NOT NULL,
    fotmob_team_id   BIGINT,
    player_name      TEXT,
    position_code    INTEGER,
    value            DOUBLE,
    substat_value    DOUBLE,
    rank             INTEGER,
    archive_key      TEXT NOT NULL,
    PRIMARY KEY (league_id, season, stat_name, fotmob_player_id)
);

CREATE TABLE IF NOT EXISTS src_fotmob_team_stat (
    league_id      BIGINT NOT NULL,
    league_name    TEXT,
    season         TEXT NOT NULL,
    season_id      BIGINT,
    stat_name      TEXT NOT NULL,
    stat_title     TEXT,
    stat_category  TEXT,
    fotmob_team_id BIGINT NOT NULL,
    team_name      TEXT,
    value          DOUBLE,
    substat_value  DOUBLE,
    rank           INTEGER,
    archive_key    TEXT NOT NULL,
    PRIMARY KEY (league_id, season, stat_name, fotmob_team_id)
);
```

- [ ] **Step 2: Create the golden fixtures**

`tests/fixtures/fotmob_players_xg_sample.json`:

```json
{
  "source": "fotmob", "endpoint": "premier-league/players", "league": "premier-league",
  "league_id": 47, "season": "2024/25", "season_id": 23685, "stat": "expected_goals",
  "stat_type": "players", "url": "https://www.fotmob.com/api/data/leagueseasondeepstats",
  "fetched_at": "2026-08-03T12:00:00Z",
  "data": {
    "seasons": [{"id": 23685, "name": "2024/2025", "leagueId": 47}],
    "leagueDetails": {"id": 47, "name": "Premier League"},
    "statsList": [
      {"name": "expected_goals", "title": "Expected goals (xG)", "category": "Attacking"},
      {"name": "goals", "title": "Goals", "category": "Attacking"}
    ],
    "statsData": [
      {"id": 292462, "teamId": 8650, "name": "Mohamed Salah", "position": 83,
       "statValue": {"name": "expected_goals", "value": 25.4}, "substatValue": {"value": 29},
       "rank": 1, "type": "players"},
      {"id": 646018, "teamId": 8456, "name": "Erling Haaland", "position": 89,
       "statValue": {"name": "expected_goals", "value": 24.1}, "substatValue": {"value": 22},
       "rank": 2, "type": "players"}
    ]
  }
}
```

`tests/fixtures/fotmob_teams_xg_sample.json`:

```json
{
  "source": "fotmob", "endpoint": "premier-league/teams", "league": "premier-league",
  "league_id": 47, "season": "2024/25", "season_id": 23685, "stat": "expected_goals_team",
  "stat_type": "teams", "url": "https://www.fotmob.com/api/data/leagueseasondeepstats",
  "fetched_at": "2026-08-03T12:00:00Z",
  "data": {
    "seasons": [{"id": 23685, "name": "2024/2025", "leagueId": 47}],
    "leagueDetails": {"id": 47, "name": "Premier League"},
    "statsList": [
      {"name": "expected_goals_team", "title": "Expected goals (xG)", "category": "Attacking"}
    ],
    "statsData": [
      {"id": 9825, "teamId": 9825, "name": "Arsenal", "position": null,
       "statValue": {"name": "expected_goals_team", "value": 72.5}, "substatValue": {"value": 69},
       "rank": 1, "type": "teams"}
    ]
  }
}
```

- [ ] **Step 3: Write the failing test**

`tests/test_fotmob_loader.py`:

```python
import json
from pathlib import Path

import pytest

from ptb.core.warehouse import db
from ptb.core.warehouse.loaders import fotmob as loader

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def con(tmp_path):
    connection = db.connect(tmp_path / "test.duckdb")
    yield connection
    connection.close()


def _load(con, name):
    payload = json.loads((FIX / name).read_text(encoding="utf-8"))
    return loader.load_fotmob(con, payload, "fotmob/" + name)


def test_fotmob_is_registered():
    from ptb.core.warehouse.load import LOADERS
    assert "fotmob" in LOADERS


def test_player_stat_loads_each_row(con):
    assert _load(con, "fotmob_players_xg_sample.json") == 2
    assert con.execute("SELECT count(*) FROM src_fotmob_player_stat").fetchone()[0] == 2


def test_player_stat_maps_columns_and_metadata(con):
    _load(con, "fotmob_players_xg_sample.json")
    row = con.execute(
        "SELECT league_name, season, stat_title, stat_category, fotmob_team_id, "
        "       player_name, position_code, value, substat_value, rank "
        "FROM src_fotmob_player_stat "
        "WHERE stat_name = 'expected_goals' AND fotmob_player_id = 292462"
    ).fetchone()
    assert row[0] == "Premier League"
    assert row[1] == "2024/25"
    assert row[2] == "Expected goals (xG)"      # looked up from statsList
    assert row[3] == "Attacking"
    assert row[4] == 8650
    assert row[5] == "Mohamed Salah"
    assert row[6] == 83
    assert row[7] == pytest.approx(25.4)
    assert row[8] == pytest.approx(29)
    assert row[9] == 1


def test_team_stat_loads_and_maps(con):
    assert _load(con, "fotmob_teams_xg_sample.json") == 1
    row = con.execute(
        "SELECT team_name, stat_title, value, rank FROM src_fotmob_team_stat "
        "WHERE stat_name = 'expected_goals_team' AND fotmob_team_id = 9825"
    ).fetchone()
    assert row[0] == "Arsenal"
    assert row[1] == "Expected goals (xG)"
    assert row[2] == pytest.approx(72.5)
    assert row[3] == 1


def test_loaders_are_idempotent(con):
    for name in ("fotmob_players_xg_sample.json", "fotmob_teams_xg_sample.json"):
        _load(con, name)
        _load(con, name)
    assert con.execute("SELECT count(*) FROM src_fotmob_player_stat").fetchone()[0] == 2
    assert con.execute("SELECT count(*) FROM src_fotmob_team_stat").fetchone()[0] == 1


def test_empty_statsdata_writes_nothing(con):
    payload = {
        "source": "fotmob", "league_id": 47, "season": "2024/25", "season_id": 23685,
        "stat": "goals", "stat_type": "players",
        "data": {"leagueDetails": {"name": "Premier League"}, "statsList": [], "statsData": []},
    }
    assert loader.load_fotmob(con, payload, "k") == 0
```

- [ ] **Step 4: Run test to verify it fails**

Run: `uv run pytest tests/test_fotmob_loader.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ptb.core.warehouse.loaders.fotmob'`

- [ ] **Step 5: Write the loader**

`packages/ptb-core/src/ptb/core/warehouse/loaders/fotmob.py`:

```python
"""FotMob payloads -> src_fotmob_player_stat / src_fotmob_team_stat.

One payload is one stat's leaderboard for one season and type. The stat's title
and category are read from the payload's statsList by matching the stat name;
the per-row value is under statValue.value and the paired secondary under
substatValue.value.
"""
from __future__ import annotations

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


def _meta(payload: dict) -> tuple:
    """(stat_title, stat_category) for this payload's stat, from statsList."""
    stat = payload["stat"]
    for entry in payload["data"].get("statsList", []):
        if entry.get("name") == stat:
            return entry.get("title"), entry.get("category")
    return None, None


def _value(row: dict, key: str) -> Optional[float]:
    obj = row.get(key) or {}
    return _f(obj.get("value"))


def _load_players(con, payload, archive_key) -> int:
    league_name = (payload["data"].get("leagueDetails") or {}).get("name")
    title, category = _meta(payload)
    rows = []
    for r in payload["data"].get("statsData", []):
        if r.get("id") is None:
            continue
        rows.append([
            payload["league_id"], league_name, payload["season"], payload.get("season_id"),
            payload["stat"], title, category, _i(r.get("id")), _i(r.get("teamId")),
            r.get("name"), _i(r.get("position")), _value(r, "statValue"),
            _value(r, "substatValue"), _i(r.get("rank")), archive_key,
        ])
    if rows:
        con.executemany(
            "INSERT OR REPLACE INTO src_fotmob_player_stat "
            "(league_id, league_name, season, season_id, stat_name, stat_title, stat_category, "
            " fotmob_player_id, fotmob_team_id, player_name, position_code, value, substat_value, "
            " rank, archive_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    return len(rows)


def _load_teams(con, payload, archive_key) -> int:
    league_name = (payload["data"].get("leagueDetails") or {}).get("name")
    title, category = _meta(payload)
    rows = []
    for r in payload["data"].get("statsData", []):
        if r.get("id") is None:
            continue
        rows.append([
            payload["league_id"], league_name, payload["season"], payload.get("season_id"),
            payload["stat"], title, category, _i(r.get("id")), r.get("name"),
            _value(r, "statValue"), _value(r, "substatValue"), _i(r.get("rank")), archive_key,
        ])
    if rows:
        con.executemany(
            "INSERT OR REPLACE INTO src_fotmob_team_stat "
            "(league_id, league_name, season, season_id, stat_name, stat_title, stat_category, "
            " fotmob_team_id, team_name, value, substat_value, rank, archive_key) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    return len(rows)


def load_fotmob(con: duckdb.DuckDBPyConnection, payload: dict, archive_key: str) -> int:
    stat_type = payload.get("stat_type")
    if stat_type == "players":
        return _load_players(con, payload, archive_key)
    if stat_type == "teams":
        return _load_teams(con, payload, archive_key)
    log.warning("unknown fotmob stat_type %r in %s", stat_type, archive_key)
    return 0


LOADERS["fotmob"] = load_fotmob
```

`packages/ptb-core/src/ptb/core/warehouse/loaders/__init__.py` — append:

```python
from . import fotmob  # noqa: F401  -- registers the fotmob loader
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_fotmob_loader.py -v`
Expected: 6 passed

- [ ] **Step 7: Commit**

```bash
git add packages/ptb-core/src/ptb/core/warehouse/ tests/fixtures/fotmob_players_xg_sample.json tests/fixtures/fotmob_teams_xg_sample.json tests/test_fotmob_loader.py
git commit -m "Load FotMob season stat-tables into the warehouse"
```

---

## Verification

After Task 2, this sequence should work end to end (one league-season keeps the live run quick):

```bash
uv run pytest

# One season across both leagues (~130 requests). --season is parsed to an int
# start year by the CLI; FotMob's _to_label normalises it back to the '2024/25'
# label. The CLI has no per-league flag, so this sweeps Premier League and
# Championship for the season.
uv run ptb ingest fotmob --season 2024/25
uv run ptb rebuild
uv run python -c "
from ptb.core.warehouse import db
con = db.connect(read_only=True)
print('PL player-stat rows:', con.execute('SELECT count(*) FROM src_fotmob_player_stat WHERE league_id = 47').fetchone()[0])
print('PL team-stat rows:', con.execute('SELECT count(*) FROM src_fotmob_team_stat WHERE league_id = 47').fetchone()[0])
print('PL distinct player stats:', con.execute('SELECT count(DISTINCT stat_name) FROM src_fotmob_player_stat WHERE league_id = 47').fetchone()[0])
"
```

Expected: several thousand PL player-stat rows (271 players × ~37 stats), a few hundred PL team-stat rows (20 teams × ~29 stats), and ~37 distinct PL player stats — plus the Championship equivalents in the same tables.

Then confirm disposability:

```bash
rm data/ptb.duckdb && uv run ptb rebuild
```

The row counts must be identical, with no network access during `rebuild`.

## Notes

**No per-league CLI flag.** As with Understat and ASA, `ptb ingest fotmob`
cannot be scoped to a single league from the CLI (`--competition` maps to a
`competitions` option FotMob ignores), so it sweeps both leagues. `--season`
works via `_to_label`. A shared `--league` flag is a natural follow-up across all
multi-league sources.

## What comes next

- **DraftKings forward odds + football-data odds enrichment:** the last dataset
  the FPL app's `fact_match_odds` needs.
- **The conformed layer:** conforming FotMob player/team ids to `dim_team` and
  player identity, which the FPL modelling port will build on.
