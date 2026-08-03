# Understat and ASA Sources — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two sources to the Plan 1 data layer — Understat (shot-level xG for the Big 5 men's leagues) and ASA (games, xG, and goals-added for NWSL/MLS/USL) — proven by resolving an Understat Big-5 match onto football-data's existing `dim_match` and an NWSL late kickoff through the ±36h window.

**Architecture:** Each source follows the Plan 1 shape exactly: a `Source` that fetches verbatim payloads into the archive (`ptb ingest`), a loader that replays them into source-faithful `src_*` tables (`ptb rebuild`), and a `resolve_*` step that maps its matches into `dim_match`. Fetching and loading never mix. Ingest is incremental by default (skip already-archived keys) with a `--refetch` override. Nothing is merged across sources — that is Plan 3.

**Tech Stack:** Python 3.12+, `uv` workspace, DuckDB, `requests`, `pytest`.

**Spec:** `docs/superpowers/specs/2026-08-03-understat-and-asa-sources-design.md`

## Global Constraints

- Python `>=3.12`. Unchanged from Plan 1.
- Seasons stored as `2024/25` for Big-5 competitions; single calendar year (`2024`) for NWSL/MLS/USL. Sources using a start year (Understat `2024`) convert at load time.
- `RawArchive`'s public API takes and returns **string keys**, never `pathlib.Path`.
- Fetching and loading never occur in the same command. `ingest` writes only the archive; `rebuild` reads only from it.
- Every loader is idempotent: loading the same archive key twice leaves the warehouse unchanged (all use `INSERT OR REPLACE`).
- Nothing is averaged or merged across sources. `src_*` tables stay source-faithful.
- Archived payloads are verbatim and self-contained — each carries its own source/competition/season/fetch metadata in an envelope.
- `data/` is gitignored. Never commit the archive or the `.duckdb` file.
- Identity-resolution failures produce recorded unresolved rows, never dropped data.
- Ingest is incremental by default; `--refetch` forces re-pull. Applies per-match for Understat and per-(league, resource, season) for ASA.

## Confirmed source facts (probed live 2026-08-03)

**Understat** — headers need `X-Requested-With: XMLHttpRequest` plus a `Referer`.
- `GET https://understat.com/getLeagueData/{league}/{year}` → `{teams, players, dates}`. Each `dates` entry: `{id, isResult, h:{id,title,short_title}, a:{...}, goals:{h,a}, xG:{h,a}, datetime:"YYYY-MM-DD HH:MM:SS", forecast:{w,d,l}}`.
- `GET https://understat.com/getMatchData/{id}` → `{rosters, shots:{h:[...], a:[...]}, tmpl}`. Each shot: `{id, minute, result, X, Y, xG, player, h_a, player_id, situation, season, shotType, match_id, h_team, a_team, h_goals, a_goals, date, player_assisted, lastAction}`.
- Leagues: `EPL, La_liga, Bundesliga, Serie_A, Ligue_1`. Season named by start year (`2024` == `2024/25`).

**ASA** — base `https://app.americansocceranalysis.com/api/v1`.
- Leagues: `nwsl, mls, uslc, usl1`.
- `GET /{league}/teams` → `[{team_id, team_name, team_short_name, team_abbreviation}]` (no season param).
- `GET /{league}/players` → `[{player_id, player_name, birth_date, nationality, primary_general_position, ..., season_name}]` (no season param; one row per player-season).
- `GET /{league}/games?season_name={year}` → `[{game_id, date_time_utc:"YYYY-MM-DD HH:MM:SS UTC", home_score, away_score, home_team_id, away_team_id, matchday, status, season_name, ...}]`.
- `GET /{league}/games/xgoals?season_name={year}` → per-game xG: `{game_id, home_team_id, home_goals, home_team_xgoals, home_player_xgoals, away_team_id, away_goals, away_team_xgoals, away_player_xgoals, home_xpoints, away_xpoints, ...}`.
- `GET /{league}/players/goals-added?season_name={year}` → `[{player_id, team_id, general_position, minutes_played, data:[{action_type, goals_added_raw, goals_added_above_avg, count_actions}]}]`.
- `GET /{league}/players/xgoals?season_name={year}` → `[{player_id, team_id, general_position, minutes_played, shots, shots_on_target, goals, xgoals, xplace, goals_minus_xgoals, key_passes, primary_assists, xassists, primary_assists_minus_xassists, goals_plus_primary_assists, xgoals_plus_xassists, points_added, xpoints_added}]`.
- `GET /{league}/players/xpass?season_name={year}` → `[{player_id, team_id, general_position, minutes_played, attempted_passes, pass_completion_percentage, xpass_completion_percentage, passes_completed_over_expected, passes_completed_over_expected_p100, avg_distance_yds, avg_vertical_distance_yds, share_team_touches, count_games}]`.

---

## File Structure

| File | Responsibility |
|---|---|
| `.../ptb/core/sources/understat.py` | Understat fetch (league + match), incremental, `understat` source |
| `.../ptb/core/sources/asa.py` | ASA fetch (teams/players/games/xgoals/player-stats), incremental, `asa` source |
| `.../ptb/core/warehouse/schema.sql` | Add `src_understat_*`, `src_asa_*`, and `dim_competition` seeds |
| `.../ptb/core/warehouse/loaders/understat.py` | Understat envelopes → `src_understat_match` / `src_understat_shot` |
| `.../ptb/core/warehouse/loaders/asa.py` | ASA envelopes → `src_asa_*` tables |
| `.../ptb/core/identity/matches.py` | Add `resolve_understat` and `resolve_asa` |
| `.../ptb/core/identity/team_aliases.yaml` | Add Understat Big-5 spellings that differ from Plan 1 canonicals |
| `.../ptb/core/warehouse/load.py` | Call the two new resolve steps in `rebuild()` |
| `.../ptb/core/cli.py` | `--refetch` on `ingest`; generalize `coverage` to all sources |
| `tests/fixtures/*.json` | Golden payloads per source per endpoint |
| `tests/test_understat_source.py`, `test_understat_loader.py` | Understat tests |
| `tests/test_asa_source.py`, `test_asa_loader.py` | ASA tests |
| `tests/test_identity_understat_asa.py` | Cross-source + NWSL resolution tests |

---

### Task 1: Understat source and incremental ingest

**Files:**
- Create: `packages/ptb-core/src/ptb/core/sources/understat.py`
- Modify: `packages/ptb-core/src/ptb/core/sources/__init__.py` (import for registration)
- Modify: `packages/ptb-core/src/ptb/core/cli.py` (add `--refetch` to `ingest`)
- Test: `tests/test_understat_source.py`

**Interfaces:**
- Consumes: `RawArchive.write`, `RawArchive.keys`, `register_source`
- Produces:
  - `LEAGUES: tuple[str, ...]` = `("EPL", "La_liga", "Bundesliga", "Serie_A", "Ligue_1")`
  - `season_label(2024) -> "2024/25"`, `parse_season("2024/25") -> 2024`
  - `archived_match_ids(archive) -> set[str]`
  - `UnderstatSource.ingest(archive, leagues=..., seasons=..., refetch=False, captured_at=None) -> list[str]`
  - League envelope shape: `{"source","endpoint":"league","league","competition","season","url","fetched_at","data"}`
  - Match envelope shape: `{"source","endpoint":"match","understat_match_id","url","fetched_at","data"}`

- [ ] **Step 1: Write the failing test**

`tests/test_understat_source.py`:

```python
import datetime as dt

import pytest

from ptb.core.archive import LocalBackend, RawArchive
from ptb.core.sources import understat as us


def test_season_label_and_parse_roundtrip():
    for year in (2014, 2019, 2024):
        assert us.parse_season(us.season_label(year)) == year
    assert us.season_label(2024) == "2024/25"
    assert us.season_label(2019) == "2019/20"


def test_leagues_map_to_plan1_competitions():
    assert us.LEAGUE_TO_COMPETITION["EPL"] == "E0"
    assert set(us.LEAGUE_TO_COMPETITION) == set(us.LEAGUES)


def _league_payload():
    return {
        "teams": {}, "players": [],
        "dates": [
            {"id": "1001", "isResult": True,
             "h": {"id": "1", "title": "Arsenal", "short_title": "ARS"},
             "a": {"id": "2", "title": "Wolverhampton Wanderers", "short_title": "WOL"},
             "goals": {"h": "2", "a": "0"}, "xG": {"h": "1.9", "a": "0.4"},
             "datetime": "2024-08-17 15:00:00",
             "forecast": {"w": "0.7", "d": "0.2", "l": "0.1"}},
            {"id": "1002", "isResult": False,
             "h": {"id": "1", "title": "Arsenal", "short_title": "ARS"},
             "a": {"id": "3", "title": "Chelsea", "short_title": "CHE"},
             "goals": {"h": "0", "a": "0"}, "xG": {"h": "0", "a": "0"},
             "datetime": "2025-05-01 15:00:00",
             "forecast": {"w": "0.4", "d": "0.3", "l": "0.3"}},
        ],
    }


def test_ingest_archives_league_then_played_matches(tmp_path, monkeypatch):
    archive = RawArchive(LocalBackend(tmp_path))
    monkeypatch.setattr(us, "_fetch_league", lambda s, lg, yr: _league_payload())
    monkeypatch.setattr(us, "_fetch_match", lambda s, mid: {"shots": {"h": [], "a": []}})

    keys = us.UnderstatSource().ingest(
        archive, leagues=["EPL"], seasons=[2024],
        captured_at=dt.datetime(2026, 8, 3, 12, 0, 0),
    )

    # one league payload + one match payload (only isResult == True is fetched)
    assert any(k.startswith("understat/league/EPL__2024-25__") for k in keys)
    assert any(k.startswith("understat/match/1001__") for k in keys)
    assert not any("/match/1002__" in k for k in keys)

    league_key = next(k for k in keys if "/league/" in k)
    env = archive.read(league_key)
    assert env["competition"] == "E0"
    assert env["season"] == "2024/25"
    assert env["data"]["dates"][0]["id"] == "1001"


def test_ingest_is_incremental_by_default(tmp_path, monkeypatch):
    archive = RawArchive(LocalBackend(tmp_path))
    monkeypatch.setattr(us, "_fetch_league", lambda s, lg, yr: _league_payload())

    calls = []
    monkeypatch.setattr(us, "_fetch_match",
                        lambda s, mid: calls.append(mid) or {"shots": {"h": [], "a": []}})

    us.UnderstatSource().ingest(archive, leagues=["EPL"], seasons=[2024],
                                captured_at=dt.datetime(2026, 8, 3, 12, 0, 0))
    us.UnderstatSource().ingest(archive, leagues=["EPL"], seasons=[2024],
                                captured_at=dt.datetime(2026, 8, 3, 13, 0, 0))

    assert calls == ["1001"]  # second run skipped the already-archived match


def test_refetch_forces_match_refetch(tmp_path, monkeypatch):
    archive = RawArchive(LocalBackend(tmp_path))
    monkeypatch.setattr(us, "_fetch_league", lambda s, lg, yr: _league_payload())
    calls = []
    monkeypatch.setattr(us, "_fetch_match",
                        lambda s, mid: calls.append(mid) or {"shots": {"h": [], "a": []}})

    us.UnderstatSource().ingest(archive, leagues=["EPL"], seasons=[2024],
                                captured_at=dt.datetime(2026, 8, 3, 12, 0, 0))
    us.UnderstatSource().ingest(archive, leagues=["EPL"], seasons=[2024], refetch=True,
                                captured_at=dt.datetime(2026, 8, 3, 13, 0, 0))

    assert calls == ["1001", "1001"]


def test_ingest_rejects_unknown_league(tmp_path):
    archive = RawArchive(LocalBackend(tmp_path))
    with pytest.raises(ValueError, match="Bundesliga2"):
        us.UnderstatSource().ingest(archive, leagues=["Bundesliga2"], seasons=[2024])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_understat_source.py -v`
Expected: FAIL — `ImportError: cannot import name 'understat'`

- [ ] **Step 3: Write understat.py**

`packages/ptb-core/src/ptb/core/sources/understat.py`:

```python
"""Understat -- shot-level xG for the Big 5 men's leagues.

Understat was reworked to a JS shell in late 2025; data comes from plain JSON
endpoints, not embedded HTML. `getLeagueData/{league}/{year}` returns
{teams, players, dates}; `getMatchData/{id}` returns {shots: {h, a}, ...}.

Ingest is two-phase: one league payload per league-season (giving the match
list and match-level xG), then one shot payload per played match. The match
phase is incremental -- a match already in the archive is skipped unless
`refetch` is set -- because a full shot sweep is ~2000 requests per season.
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

BASE = "https://understat.com"
LEAGUES = ("EPL", "La_liga", "Bundesliga", "Serie_A", "Ligue_1")
LEAGUE_TO_COMPETITION = {
    "EPL": "E0", "La_liga": "SP1", "Bundesliga": "D1", "Serie_A": "I1", "Ligue_1": "F1",
}
_POLITENESS_SECONDS = 1.0


def season_label(start_year: int) -> str:
    """2024 -> '2024/25'."""
    return "{}/{:02d}".format(start_year, (start_year + 1) % 100)


def parse_season(label: str) -> int:
    """'2024/25' -> 2024."""
    return int(label.split("/")[0])


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": config.USER_AGENT,
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
    })
    return session


def _get_json(session: requests.Session, url: str, referer: str, retries: int = 3) -> Any:
    last: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            resp = session.get(url, headers={"Referer": referer}, timeout=config.REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as exc:
            last = exc
            if attempt < retries:
                time.sleep((2 ** attempt) + random.random())
    raise RuntimeError("failed to fetch {}: {}".format(url, last))


def _fetch_league(session: requests.Session, league: str, start_year: int) -> Dict[str, Any]:
    url = "{}/getLeagueData/{}/{}".format(BASE, league, start_year)
    return _get_json(session, url, "{}/league/{}/{}".format(BASE, league, start_year))


def _fetch_match(session: requests.Session, match_id: str) -> Dict[str, Any]:
    url = "{}/getMatchData/{}".format(BASE, match_id)
    return _get_json(session, url, "{}/match/{}".format(BASE, match_id))


def archived_match_ids(archive: RawArchive) -> Set[str]:
    """Understat match ids already present in the archive."""
    prefix = "understat/match/"
    ids: Set[str] = set()
    for key in archive.keys(source="understat", endpoint="match"):
        name = key[len(prefix):]
        ids.add(name.split("__", 1)[0])
    return ids


@register_source
class UnderstatSource:
    name = "understat"

    def ingest(
        self,
        archive: RawArchive,
        leagues: Optional[Sequence[str]] = None,
        seasons: Optional[Sequence[int]] = None,
        refetch: bool = False,
        captured_at: Optional[dt.datetime] = None,
        **_ignored,
    ) -> List[str]:
        leagues = list(leagues or LEAGUES)
        seasons = list(seasons or [dt.date.today().year - (0 if dt.date.today().month >= 7 else 1)])
        captured_at = captured_at or dt.datetime.utcnow().replace(microsecond=0)

        unknown = [lg for lg in leagues if lg not in LEAGUE_TO_COMPETITION]
        if unknown:
            raise ValueError("unknown league(s): {}".format(", ".join(unknown)))

        session = _session()
        seen = set() if refetch else archived_match_ids(archive)
        written: List[str] = []

        for start_year in seasons:
            season = season_label(start_year)
            for league in leagues:
                data = _fetch_league(session, league, start_year)
                url = "{}/getLeagueData/{}/{}".format(BASE, league, start_year)
                envelope = {
                    "source": self.name, "endpoint": "league",
                    "league": league, "competition": LEAGUE_TO_COMPETITION[league],
                    "season": season, "url": url,
                    "fetched_at": captured_at.isoformat() + "Z", "data": data,
                }
                written.append(archive.write(
                    self.name, "league", envelope,
                    label="{}__{}".format(league, season.replace("/", "-")),
                    captured_at=captured_at,
                ))
                log.info("archived understat league %s %s", league, season)
                time.sleep(_POLITENESS_SECONDS)

                match_ids = [str(d["id"]) for d in data.get("dates", []) if d.get("isResult")]
                for match_id in match_ids:
                    if match_id in seen:
                        continue
                    shot_data = _fetch_match(session, match_id)
                    match_env = {
                        "source": self.name, "endpoint": "match",
                        "understat_match_id": match_id,
                        "url": "{}/getMatchData/{}".format(BASE, match_id),
                        "fetched_at": captured_at.isoformat() + "Z", "data": shot_data,
                    }
                    written.append(archive.write(
                        self.name, "match", match_env,
                        label=match_id, captured_at=captured_at,
                    ))
                    seen.add(match_id)
                    time.sleep(_POLITENESS_SECONDS)
                log.info("archived understat shots %s %s (%d matches)", league, season, len(match_ids))

        return written
```

Note `season_label` uses `{:02d}` so 2019 gives `2019/20`, matching Plan 1's football-data convention.

- [ ] **Step 4: Register the module and add `--refetch`**

Append to `packages/ptb-core/src/ptb/core/sources/__init__.py`:

```python
from . import understat  # noqa: F401,E402  -- imported for registration side effect
```

In `packages/ptb-core/src/ptb/core/cli.py`, add the `--refetch` argument to the `ingest` subparser (immediately after the existing `--season` line):

```python
    ingest.add_argument("--refetch", action="store_true",
                        help="re-fetch payloads already in the archive")
```

and pass it through in `_cmd_ingest` — replace the `options = {}` block's end (after the `--season` handling) so the function reads:

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
    if args.refetch:
        options["refetch"] = True

    total = 0
    for name in names:
        keys = get_source(name).ingest(archive, **options)
        print("{}: archived {} payload(s)".format(name, len(keys)))
        total += len(keys)

    return 0 if total else 1
```

Note: football-data's `ingest` already accepts `**_ignored`, so a stray `refetch=True` is harmless for sources that do not use it. Seasons are parsed with football-data's `parse_season` (identical `2024/25 -> 2024` semantics), so `--season 2024/25` works for Understat too.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_understat_source.py -v`
Expected: 6 passed

- [ ] **Step 6: Commit**

```bash
git add packages/ptb-core/src/ptb/core/sources/ packages/ptb-core/src/ptb/core/cli.py tests/test_understat_source.py
git commit -m "Ingest Understat leagues and match shots into the archive"
```

---

### Task 2: Understat match loader

**Files:**
- Modify: `packages/ptb-core/src/ptb/core/warehouse/schema.sql` (add `src_understat_match`)
- Create: `packages/ptb-core/src/ptb/core/warehouse/loaders/understat.py`
- Modify: `packages/ptb-core/src/ptb/core/warehouse/loaders/__init__.py` (import understat)
- Create: `tests/fixtures/understat_league_epl_2024_sample.json`
- Test: `tests/test_understat_loader.py` (match portion)

**Interfaces:**
- Consumes: `LOADERS` dict from `warehouse.load`
- Produces: `load_understat_league(con, payload: dict, archive_key: str) -> int` registered as `LOADERS["understat_league"]` via the endpoint dispatch (see Step 4)

**Note on loader dispatch:** Plan 1 keyed `LOADERS` by source name. Understat has two payload endpoints (`league`, `match`) with different shapes. This task generalizes dispatch to `"{source}"` still, but the understat loader inspects `payload["endpoint"]` and routes internally. This keeps `rebuild()` unchanged.

- [ ] **Step 1: Extend the schema**

Append to `packages/ptb-core/src/ptb/core/warehouse/schema.sql`:

```sql
-- --------------------------------------------------------------- understat

CREATE TABLE IF NOT EXISTS src_understat_match (
    understat_match_id TEXT PRIMARY KEY,
    competition   TEXT NOT NULL,
    season        TEXT NOT NULL,
    kickoff       TIMESTAMP,
    home_team     TEXT NOT NULL,
    away_team     TEXT NOT NULL,
    home_goals    INTEGER,
    away_goals    INTEGER,
    home_xg       DOUBLE,
    away_xg       DOUBLE,
    forecast_w    DOUBLE,
    forecast_d    DOUBLE,
    forecast_l    DOUBLE,
    archive_key   TEXT NOT NULL
);
```

- [ ] **Step 2: Create the golden league fixture**

`tests/fixtures/understat_league_epl_2024_sample.json` (trimmed real shape, two matches — one played, one not):

```json
{
  "source": "understat",
  "endpoint": "league",
  "league": "EPL",
  "competition": "E0",
  "season": "2024/25",
  "url": "https://understat.com/getLeagueData/EPL/2024",
  "fetched_at": "2026-08-03T12:00:00Z",
  "data": {
    "teams": {},
    "players": [],
    "dates": [
      {"id": "1001", "isResult": true,
       "h": {"id": "1", "title": "Arsenal", "short_title": "ARS"},
       "a": {"id": "2", "title": "Wolverhampton Wanderers", "short_title": "WOL"},
       "goals": {"h": "2", "a": "0"}, "xG": {"h": "1.94", "a": "0.41"},
       "datetime": "2024-08-17 15:00:00",
       "forecast": {"w": "0.71", "d": "0.19", "l": "0.10"}},
      {"id": "1002", "isResult": false,
       "h": {"id": "1", "title": "Arsenal", "short_title": "ARS"},
       "a": {"id": "3", "title": "Chelsea", "short_title": "CHE"},
       "goals": {"h": "0", "a": "0"}, "xG": {"h": "0", "a": "0"},
       "datetime": "2025-05-01 20:00:00",
       "forecast": {"w": "0.40", "d": "0.30", "l": "0.30"}}
    ]
  }
}
```

- [ ] **Step 3: Write the failing test**

`tests/test_understat_loader.py`:

```python
import datetime as dt
import json
from pathlib import Path

import pytest

from ptb.core.warehouse import db
from ptb.core.warehouse.loaders import understat as loader

LEAGUE_FIXTURE = Path(__file__).parent / "fixtures" / "understat_league_epl_2024_sample.json"


@pytest.fixture
def con(tmp_path):
    connection = db.connect(tmp_path / "test.duckdb")
    yield connection
    connection.close()


@pytest.fixture
def league_payload():
    return json.loads(LEAGUE_FIXTURE.read_text(encoding="utf-8"))


def test_league_loader_writes_every_dated_match(con, league_payload):
    rows = loader.load_understat(con, league_payload, "understat/league/k.json.gz")
    assert rows == 2
    assert con.execute("SELECT count(*) FROM src_understat_match").fetchone()[0] == 2


def test_league_loader_maps_columns(con, league_payload):
    loader.load_understat(con, league_payload, "understat/league/k.json.gz")
    row = con.execute(
        "SELECT competition, season, kickoff, home_team, away_team, "
        "       home_goals, away_goals, home_xg, away_xg, forecast_w "
        "FROM src_understat_match WHERE understat_match_id = '1001'"
    ).fetchone()
    assert row[0] == "E0"
    assert row[1] == "2024/25"
    assert row[2] == dt.datetime(2024, 8, 17, 15, 0)
    assert (row[3], row[4]) == ("Arsenal", "Wolverhampton Wanderers")
    assert (row[5], row[6]) == (2, 0)
    assert row[7] == pytest.approx(1.94)
    assert row[8] == pytest.approx(0.41)
    assert row[9] == pytest.approx(0.71)


def test_league_loader_is_idempotent(con, league_payload):
    loader.load_understat(con, league_payload, "k")
    loader.load_understat(con, league_payload, "k")
    assert con.execute("SELECT count(*) FROM src_understat_match").fetchone()[0] == 2


def test_understat_is_registered():
    from ptb.core.warehouse.load import LOADERS
    assert "understat" in LOADERS
```

- [ ] **Step 4: Run test to verify it fails**

Run: `uv run pytest tests/test_understat_loader.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ptb.core.warehouse.loaders.understat'`

- [ ] **Step 5: Write the loader**

`packages/ptb-core/src/ptb/core/warehouse/loaders/understat.py`:

```python
"""Understat payloads -> src_understat_match / src_understat_shot.

Two payload shapes share one loader, routed on payload["endpoint"]:
- "league" -> one row per dated match (match-level xG)
- "match"  -> one row per shot

Understat sends every numeric value as a string; each is coerced defensively
so a stray empty string never aborts a rebuild.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any, Optional

import duckdb

from ..load import LOADERS

log = logging.getLogger(__name__)

_MATCH_COLUMNS = [
    "understat_match_id", "competition", "season", "kickoff", "home_team", "away_team",
    "home_goals", "away_goals", "home_xg", "away_xg", "forecast_w", "forecast_d",
    "forecast_l", "archive_key",
]


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


def _dttm(value: Any) -> Optional[dt.datetime]:
    if not value:
        return None
    try:
        return dt.datetime.strptime(str(value).strip(), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _load_league(con: duckdb.DuckDBPyConnection, payload: dict, archive_key: str) -> int:
    competition = payload["competition"]
    season = payload["season"]
    rows = []
    for entry in payload["data"].get("dates", []):
        if not entry.get("isResult"):
            continue
        home = (entry.get("h") or {}).get("title")
        away = (entry.get("a") or {}).get("title")
        if not home or not away:
            continue
        goals = entry.get("goals") or {}
        xg = entry.get("xG") or {}
        forecast = entry.get("forecast") or {}
        rows.append([
            str(entry["id"]), competition, season, _dttm(entry.get("datetime")),
            home, away, _i(goals.get("h")), _i(goals.get("a")),
            _f(xg.get("h")), _f(xg.get("a")),
            _f(forecast.get("w")), _f(forecast.get("d")), _f(forecast.get("l")),
            archive_key,
        ])
    if rows:
        con.executemany(
            "INSERT OR REPLACE INTO src_understat_match ({}) VALUES ({})".format(
                ", ".join(_MATCH_COLUMNS), ", ".join("?" * len(_MATCH_COLUMNS))
            ),
            rows,
        )
    return len(rows)


def load_understat(con: duckdb.DuckDBPyConnection, payload: dict, archive_key: str) -> int:
    endpoint = payload.get("endpoint")
    if endpoint == "league":
        return _load_league(con, payload, archive_key)
    if endpoint == "match":
        from .understat_shots import load_match_shots
        return load_match_shots(con, payload, archive_key)
    log.warning("unknown understat endpoint %r in %s", endpoint, archive_key)
    return 0


LOADERS["understat"] = load_understat
```

`packages/ptb-core/src/ptb/core/warehouse/loaders/__init__.py` — append:

```python
from . import understat  # noqa: F401  -- registers the understat loader
```

Note: `load_understat` references `.understat_shots.load_match_shots`, created in Task 3. Until then the `"match"` branch is unused by these tests (fixtures are `league` only), and the import is inside the branch so the module loads cleanly.

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_understat_loader.py -v`
Expected: 4 passed

- [ ] **Step 7: Commit**

```bash
git add packages/ptb-core/src/ptb/core/warehouse/ tests/fixtures/understat_league_epl_2024_sample.json tests/test_understat_loader.py
git commit -m "Load Understat match-level xG into the warehouse"
```

---

### Task 3: Understat shot loader

**Files:**
- Modify: `packages/ptb-core/src/ptb/core/warehouse/schema.sql` (add `src_understat_shot`)
- Create: `packages/ptb-core/src/ptb/core/warehouse/loaders/understat_shots.py`
- Create: `tests/fixtures/understat_match_1001_sample.json`
- Test: `tests/test_understat_loader.py` (add shot tests)

**Interfaces:**
- Consumes: `payload["endpoint"] == "match"` envelope from Task 1
- Produces: `load_match_shots(con, payload: dict, archive_key: str) -> int`

- [ ] **Step 1: Extend the schema**

Append to `packages/ptb-core/src/ptb/core/warehouse/schema.sql`:

```sql
CREATE TABLE IF NOT EXISTS src_understat_shot (
    understat_shot_id  TEXT PRIMARY KEY,
    understat_match_id TEXT NOT NULL,
    minute        INTEGER,
    player        TEXT,
    player_id     TEXT,
    team          TEXT,
    home_away     TEXT,
    xg            DOUBLE,
    result        TEXT,
    situation     TEXT,
    shot_type     TEXT,
    x             DOUBLE,
    y             DOUBLE,
    assist_player TEXT,
    last_action   TEXT,
    archive_key   TEXT NOT NULL
);
```

- [ ] **Step 2: Create the golden match fixture**

`tests/fixtures/understat_match_1001_sample.json` (two shots, one per side — real shape):

```json
{
  "source": "understat",
  "endpoint": "match",
  "understat_match_id": "1001",
  "url": "https://understat.com/getMatchData/1001",
  "fetched_at": "2026-08-03T12:00:00Z",
  "data": {
    "shots": {
      "h": [
        {"id": "9001", "minute": "23", "result": "Goal", "X": "0.88", "Y": "0.52",
         "xG": "0.34", "player": "Bukayo Saka", "h_a": "h", "player_id": "500",
         "situation": "OpenPlay", "season": "2024", "shotType": "LeftFoot",
         "match_id": "1001", "h_team": "Arsenal", "a_team": "Wolverhampton Wanderers",
         "h_goals": "2", "a_goals": "0", "date": "2024-08-17 15:00:00",
         "player_assisted": "Martin Odegaard", "lastAction": "Pass"}
      ],
      "a": [
        {"id": "9002", "minute": "70", "result": "MissedShots", "X": "0.80", "Y": "0.40",
         "xG": "0.07", "player": "Matheus Cunha", "h_a": "a", "player_id": "600",
         "situation": "FromCorner", "season": "2024", "shotType": "Head",
         "match_id": "1001", "h_team": "Arsenal", "a_team": "Wolverhampton Wanderers",
         "h_goals": "2", "a_goals": "0", "date": "2024-08-17 15:00:00",
         "player_assisted": null, "lastAction": "Aerial"}
      ]
    }
  }
}
```

- [ ] **Step 3: Add the failing shot tests**

Append to `tests/test_understat_loader.py`:

```python
MATCH_FIXTURE = Path(__file__).parent / "fixtures" / "understat_match_1001_sample.json"


@pytest.fixture
def match_payload():
    return json.loads(MATCH_FIXTURE.read_text(encoding="utf-8"))


def test_shot_loader_writes_all_shots_both_sides(con, match_payload):
    rows = loader.load_understat(con, match_payload, "understat/match/1001__k.json.gz")
    assert rows == 2
    assert con.execute("SELECT count(*) FROM src_understat_shot").fetchone()[0] == 2


def test_shot_loader_maps_columns(con, match_payload):
    loader.load_understat(con, match_payload, "k")
    row = con.execute(
        "SELECT understat_match_id, minute, player, player_id, team, home_away, "
        "       xg, result, situation, shot_type, x, y, assist_player "
        "FROM src_understat_shot WHERE understat_shot_id = '9001'"
    ).fetchone()
    assert row[0] == "1001"
    assert row[1] == 23
    assert row[2] == "Bukayo Saka"
    assert row[3] == "500"
    assert row[4] == "Arsenal"
    assert row[5] == "h"
    assert row[6] == pytest.approx(0.34)
    assert (row[7], row[8], row[9]) == ("Goal", "OpenPlay", "LeftFoot")
    assert (row[10], row[11]) == (pytest.approx(0.88), pytest.approx(0.52))
    assert row[12] == "Martin Odegaard"


def test_shot_loader_uses_correct_team_per_side(con, match_payload):
    loader.load_understat(con, match_payload, "k")
    away = con.execute(
        "SELECT team, home_away, assist_player FROM src_understat_shot "
        "WHERE understat_shot_id = '9002'"
    ).fetchone()
    assert away == ("Wolverhampton Wanderers", "a", None)


def test_shot_loader_is_idempotent(con, match_payload):
    loader.load_understat(con, match_payload, "k")
    loader.load_understat(con, match_payload, "k")
    assert con.execute("SELECT count(*) FROM src_understat_shot").fetchone()[0] == 2
```

- [ ] **Step 4: Run test to verify it fails**

Run: `uv run pytest tests/test_understat_loader.py -k shot -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ptb.core.warehouse.loaders.understat_shots'`

- [ ] **Step 5: Write the shot loader**

`packages/ptb-core/src/ptb/core/warehouse/loaders/understat_shots.py`:

```python
"""Understat match payload -> src_understat_shot.

Each shot carries h_team and a_team plus h_a; the shooting team is selected by
h_a so the row records which club actually took the shot.
"""
from __future__ import annotations

from typing import Any, Optional

import duckdb

_SHOT_COLUMNS = [
    "understat_shot_id", "understat_match_id", "minute", "player", "player_id",
    "team", "home_away", "xg", "result", "situation", "shot_type", "x", "y",
    "assist_player", "last_action", "archive_key",
]


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


def load_match_shots(con: duckdb.DuckDBPyConnection, payload: dict, archive_key: str) -> int:
    match_id = str(payload["understat_match_id"])
    shots = (payload.get("data") or {}).get("shots") or {}
    rows = []
    for side in ("h", "a"):
        for shot in shots.get(side, []):
            team = shot.get("h_team") if side == "h" else shot.get("a_team")
            rows.append([
                str(shot["id"]), match_id, _i(shot.get("minute")),
                shot.get("player"), shot.get("player_id"), team, side,
                _f(shot.get("xG")), shot.get("result"), shot.get("situation"),
                shot.get("shotType"), _f(shot.get("X")), _f(shot.get("Y")),
                shot.get("player_assisted"), shot.get("lastAction"), archive_key,
            ])
    if rows:
        con.executemany(
            "INSERT OR REPLACE INTO src_understat_shot ({}) VALUES ({})".format(
                ", ".join(_SHOT_COLUMNS), ", ".join("?" * len(_SHOT_COLUMNS))
            ),
            rows,
        )
    return len(rows)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_understat_loader.py -v`
Expected: 8 passed

- [ ] **Step 7: Commit**

```bash
git add packages/ptb-core/src/ptb/core/warehouse/ tests/fixtures/understat_match_1001_sample.json tests/test_understat_loader.py
git commit -m "Load Understat shot-level xG into the warehouse"
```

---

### Task 4: Understat match resolution and cross-source identity

**Files:**
- Modify: `packages/ptb-core/src/ptb/core/identity/matches.py` (add `resolve_understat`)
- Modify: `packages/ptb-core/src/ptb/core/warehouse/load.py` (call it in `rebuild`)
- Modify: `packages/ptb-core/src/ptb/core/identity/team_aliases.yaml` (Understat spellings)
- Test: `tests/test_identity_understat_asa.py` (understat portion)

**Interfaces:**
- Consumes: `resolve_match` (Plan 1), `src_understat_match`
- Produces: `resolve_understat(con) -> int`

- [ ] **Step 1: Write the failing test**

`tests/test_identity_understat_asa.py`:

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


def _insert_understat(con, mid, comp, season, kickoff, home, away):
    con.execute(
        "INSERT INTO src_understat_match "
        "(understat_match_id, competition, season, kickoff, home_team, away_team, archive_key) "
        "VALUES (?, ?, ?, ?, ?, ?, 'k')",
        [mid, comp, season, kickoff, home, away],
    )


def test_resolve_understat_maps_loaded_matches(con):
    _insert_understat(con, "1001", "E0", "2024/25",
                      dt.datetime(2024, 8, 17, 15, 0), "Arsenal", "Wolverhampton Wanderers")
    resolved = matches.resolve_understat(con)
    assert resolved == 1
    assert con.execute("SELECT count(*) FROM dim_match").fetchone()[0] == 1


def test_understat_and_footballdata_resolve_to_one_match(con):
    """The headline cross-source test: the same fixture from two sources must
    land on a single dim_match, so a cross-source join returns a row."""
    # football-data row (as Plan 1 loads it)
    con.execute(
        "INSERT INTO src_footballdata_match "
        "(competition, season, match_date, kickoff_time, home_team, away_team, archive_key) "
        "VALUES ('E0', '2024/25', DATE '2024-08-17', TIME '15:00', 'Arsenal', 'Wolves', 'fd')"
    )
    matches.resolve_footballdata(con)

    # understat row for the same match, its own spelling of the away side
    _insert_understat(con, "1001", "E0", "2024/25",
                      dt.datetime(2024, 8, 17, 15, 0), "Arsenal", "Wolverhampton Wanderers")
    matches.resolve_understat(con)

    assert con.execute("SELECT count(*) FROM dim_match").fetchone()[0] == 1
    sources = con.execute(
        "SELECT count(DISTINCT source) FROM map_match_source"
    ).fetchone()[0]
    assert sources == 2


def test_resolve_understat_is_idempotent(con):
    _insert_understat(con, "1001", "E0", "2024/25",
                      dt.datetime(2024, 8, 17, 15, 0), "Arsenal", "Wolverhampton Wanderers")
    matches.resolve_understat(con)
    matches.resolve_understat(con)
    assert con.execute("SELECT count(*) FROM dim_match").fetchone()[0] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_identity_understat_asa.py -v`
Expected: FAIL — `AttributeError: module 'ptb.core.identity.matches' has no attribute 'resolve_understat'`

- [ ] **Step 3: Add Understat spellings to the alias file**

Most Understat Big-5 spellings already conform: "Arsenal" is identical to football-data's, and "Wolverhampton Wanderers" normalizes to the same canonical as football-data's "Wolves" (via Plan 1's existing `wolves` alias), because `canonical_name` maps both to `Wolverhampton Wanderers` and `normalize_name` collapses them to the same key. **The tests in this task therefore pass without any alias change.**

The one confirmed real-data divergence is Paris Saint-Germain: football-data's canonical is `Paris Saint-Germain`, whose normalized key is `paris saintgermain` (the hyphen is stripped, leaving no space), while Understat writes it with a space, normalizing to `paris saint germain` — a different key. Append this to `packages/ptb-core/src/ptb/core/identity/team_aliases.yaml` so the two conform:

```yaml
# Understat spellings that normalize differently from football-data canonicals
"paris saint germain": Paris Saint-Germain
```

After Task 1's live `ptb ingest understat` run, scan the resulting `dim_team` for any club that resolved to two rows across sources and add an alias for each — but PSG is the only one expected for the Big 5.

- [ ] **Step 4: Write `resolve_understat`**

Append to `packages/ptb-core/src/ptb/core/identity/matches.py`:

```python
def resolve_understat(con: duckdb.DuckDBPyConnection) -> int:
    """Resolve every understat match into dim_match. Idempotent.

    Big-5 matches resolve against football-data's existing dim_match rows via
    the +/-36h window, which is the first cross-source match identity in the
    project.
    """
    rows = con.execute(
        "SELECT understat_match_id, competition, season, kickoff, home_team, away_team "
        "FROM src_understat_match WHERE kickoff IS NOT NULL "
        "ORDER BY kickoff, understat_match_id"
    ).fetchall()

    resolved = 0
    for match_id, competition, season, kickoff, home, away in rows:
        if resolve_match(
            con, source="understat", source_match_id=str(match_id),
            competition=competition, season=season, kickoff=kickoff,
            home_team=home, away_team=away,
        ) is not None:
            resolved += 1
    return resolved
```

- [ ] **Step 5: Wire it into `rebuild`**

In `packages/ptb-core/src/ptb/core/warehouse/load.py`, extend the identity block near the end of `rebuild()` (added in Plan 1) so it reads:

```python
    from ..identity import matches as identity_matches

    if written.get("footballdata"):
        resolved = identity_matches.resolve_footballdata(con)
        log.info("resolved %d football-data matches into dim_match", resolved)

    if written.get("understat"):
        resolved = identity_matches.resolve_understat(con)
        log.info("resolved %d understat matches into dim_match", resolved)

    return written
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_identity_understat_asa.py -v`
Expected: 3 passed

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass (Plan 1's 60 + the new understat tests)

- [ ] **Step 8: Commit**

```bash
git add packages/ptb-core/src/ptb/core/ tests/test_identity_understat_asa.py
git commit -m "Resolve Understat matches, including cross-source onto football-data"
```

---

### Task 5: ASA source and incremental ingest

**Files:**
- Create: `packages/ptb-core/src/ptb/core/sources/asa.py`
- Modify: `packages/ptb-core/src/ptb/core/sources/__init__.py` (import for registration)
- Test: `tests/test_asa_source.py`

**Interfaces:**
- Consumes: `RawArchive.write`, `RawArchive.keys`, `register_source`
- Produces:
  - `LEAGUES: tuple[str, ...]` = `("nwsl", "mls", "uslc", "usl1")`
  - `LEAGUE_TO_COMPETITION: dict[str, str]`
  - `SEASONAL_RESOURCES`, `REFERENCE_RESOURCES` tuples
  - `AsaSource.ingest(archive, leagues=..., seasons=..., refetch=False, captured_at=None) -> list[str]`
  - Envelope shape: `{"source":"asa","endpoint":"{league}/{resource}","league","resource","season","url","fetched_at","data"}`

- [ ] **Step 1: Write the failing test**

`tests/test_asa_source.py`:

```python
import datetime as dt

import pytest

from ptb.core.archive import LocalBackend, RawArchive
from ptb.core.sources import asa


def test_leagues_map_to_competitions():
    assert asa.LEAGUE_TO_COMPETITION == {
        "nwsl": "NWSL", "mls": "MLS", "uslc": "USLC", "usl1": "USL1",
    }


def test_ingest_archives_reference_and_seasonal_resources(tmp_path, monkeypatch):
    archive = RawArchive(LocalBackend(tmp_path))

    def fake_get(session, league, resource, season):
        return [{"stub": True, "league": league, "resource": resource, "season": season}]

    monkeypatch.setattr(asa, "_fetch", fake_get)

    keys = asa.AsaSource().ingest(
        archive, leagues=["nwsl"], seasons=[2024],
        captured_at=dt.datetime(2026, 8, 3, 12, 0, 0),
    )

    # reference resources (no season) + seasonal resources (one per season)
    assert any(k.startswith("asa/nwsl/teams/") for k in keys)
    assert any(k.startswith("asa/nwsl/players/") for k in keys)
    assert any(k.startswith("asa/nwsl/games/2024__") for k in keys)
    assert any(k.startswith("asa/nwsl/games-xgoals/2024__") for k in keys)
    assert any(k.startswith("asa/nwsl/players-goals-added/2024__") for k in keys)

    env = archive.read(next(k for k in keys if "/games/2024__" in k))
    assert env["league"] == "nwsl"
    assert env["resource"] == "games"
    assert env["season"] == "2024"


def test_seasonal_ingest_is_incremental(tmp_path, monkeypatch):
    archive = RawArchive(LocalBackend(tmp_path))
    calls = []
    monkeypatch.setattr(asa, "_fetch",
                        lambda s, lg, res, season: calls.append((res, season)) or [])

    asa.AsaSource().ingest(archive, leagues=["nwsl"], seasons=[2024],
                           captured_at=dt.datetime(2026, 8, 3, 12, 0, 0))
    first = list(calls)
    asa.AsaSource().ingest(archive, leagues=["nwsl"], seasons=[2024],
                           captured_at=dt.datetime(2026, 8, 3, 13, 0, 0))

    # reference resources refetch (they carry no season); seasonal ones are skipped
    seasonal_second = [c for c in calls[len(first):] if c[1] is not None]
    assert seasonal_second == []


def test_refetch_repulls_seasonal(tmp_path, monkeypatch):
    archive = RawArchive(LocalBackend(tmp_path))
    calls = []
    monkeypatch.setattr(asa, "_fetch",
                        lambda s, lg, res, season: calls.append((res, season)) or [])

    asa.AsaSource().ingest(archive, leagues=["nwsl"], seasons=[2024],
                           captured_at=dt.datetime(2026, 8, 3, 12, 0, 0))
    n = len(calls)
    asa.AsaSource().ingest(archive, leagues=["nwsl"], seasons=[2024], refetch=True,
                           captured_at=dt.datetime(2026, 8, 3, 13, 0, 0))
    seasonal_second = [c for c in calls[n:] if c[1] is not None]
    assert ("games", "2024") in seasonal_second


def test_ingest_rejects_unknown_league(tmp_path):
    archive = RawArchive(LocalBackend(tmp_path))
    with pytest.raises(ValueError, match="eredivisie"):
        asa.AsaSource().ingest(archive, leagues=["eredivisie"], seasons=[2024])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_asa_source.py -v`
Expected: FAIL — `ImportError: cannot import name 'asa'`

- [ ] **Step 3: Write asa.py**

`packages/ptb-core/src/ptb/core/sources/asa.py`:

```python
"""ASA (American Soccer Analysis) -- games, xG and goals-added for the US leagues.

Free documented REST API at app.americansocceranalysis.com/api/v1/{league}/...
Reference resources (teams, players) carry no season and are re-fetched each run
(they are small). Seasonal resources (games and the per-season stat tables) are
incremental: a (league, resource, season) already archived is skipped unless
`refetch` is set.

Games and players are keyed by hashed ids, not names, so resolution later joins
games to the teams table for names.
"""
from __future__ import annotations

import datetime as dt
import logging
import random
import time
from typing import Any, List, Optional, Sequence

import requests

from .. import config
from ..archive import RawArchive
from .registry import register_source

log = logging.getLogger(__name__)

BASE = "https://app.americansocceranalysis.com/api/v1"
LEAGUES = ("nwsl", "mls", "uslc", "usl1")
LEAGUE_TO_COMPETITION = {"nwsl": "NWSL", "mls": "MLS", "uslc": "USLC", "usl1": "USL1"}

# resource -> API path segment. Endpoint label in the archive replaces "/" with "-".
REFERENCE_RESOURCES = ("teams", "players")
SEASONAL_RESOURCES = (
    "games", "games/xgoals",
    "players/goals-added", "players/xgoals", "players/xpass",
)
_POLITENESS_SECONDS = 0.5


def _resource_label(resource: str) -> str:
    """'games/xgoals' -> 'games-xgoals' for use as an archive endpoint segment."""
    return resource.replace("/", "-")


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": config.USER_AGENT})
    return session


def _fetch(session: requests.Session, league: str, resource: str,
           season: Optional[str], retries: int = 3) -> Any:
    url = "{}/{}/{}".format(BASE, league, resource)
    params = {"season_name": season} if season else None
    last: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            resp = session.get(url, params=params, timeout=config.REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as exc:
            last = exc
            if attempt < retries:
                time.sleep((2 ** attempt) + random.random())
    raise RuntimeError("failed to fetch {} {}: {}".format(url, params, last))


def _seasonal_archived(archive: RawArchive, league: str, resource: str, season: str) -> bool:
    endpoint = "{}/{}".format(league, _resource_label(resource))
    prefix = "asa/{}/{}__".format(endpoint, season)
    return any(k.startswith(prefix) for k in archive.keys(source="asa", endpoint=endpoint))


@register_source
class AsaSource:
    name = "asa"

    def ingest(
        self,
        archive: RawArchive,
        leagues: Optional[Sequence[str]] = None,
        seasons: Optional[Sequence[int]] = None,
        refetch: bool = False,
        captured_at: Optional[dt.datetime] = None,
        **_ignored,
    ) -> List[str]:
        leagues = list(leagues or LEAGUES)
        seasons = list(seasons or [dt.date.today().year])
        captured_at = captured_at or dt.datetime.utcnow().replace(microsecond=0)

        unknown = [lg for lg in leagues if lg not in LEAGUE_TO_COMPETITION]
        if unknown:
            raise ValueError("unknown league(s): {}".format(", ".join(unknown)))

        session = _session()
        written: List[str] = []

        for league in leagues:
            # Reference resources: no season, always refreshed (small).
            for resource in REFERENCE_RESOURCES:
                data = _fetch(session, league, resource, None)
                written.append(self._archive(
                    archive, league, resource, None, data, captured_at))
                time.sleep(_POLITENESS_SECONDS)

            for start_year in seasons:
                season = str(start_year)
                for resource in SEASONAL_RESOURCES:
                    if not refetch and _seasonal_archived(archive, league, resource, season):
                        continue
                    data = _fetch(session, league, resource, season)
                    written.append(self._archive(
                        archive, league, resource, season, data, captured_at))
                    time.sleep(_POLITENESS_SECONDS)
                log.info("archived asa %s %s", league, season)

        return written

    def _archive(self, archive, league, resource, season, data, captured_at) -> str:
        endpoint = "{}/{}".format(league, _resource_label(resource))
        envelope = {
            "source": self.name, "endpoint": endpoint,
            "league": league, "resource": resource, "season": season,
            "url": "{}/{}/{}".format(BASE, league, resource),
            "fetched_at": captured_at.isoformat() + "Z", "data": data,
        }
        return archive.write(
            self.name, endpoint, envelope,
            label=season, captured_at=captured_at,
        )
```

Note: when `season` is `None` (reference resources), `archive.write` omits the label, so their keys are `asa/{league}/teams/{stamp}.json.gz`. The `endpoint` carries a slash, which `RawArchive`/`LocalBackend` handle as nested paths.

- [ ] **Step 4: Register the module**

Append to `packages/ptb-core/src/ptb/core/sources/__init__.py`:

```python
from . import asa  # noqa: F401,E402  -- imported for registration side effect
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_asa_source.py -v`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add packages/ptb-core/src/ptb/core/sources/ tests/test_asa_source.py
git commit -m "Ingest ASA leagues into the archive"
```

---

### Task 6: ASA reference and game loaders

**Files:**
- Modify: `packages/ptb-core/src/ptb/core/warehouse/schema.sql` (dim_competition seeds + `src_asa_team`, `src_asa_player`, `src_asa_game`, `src_asa_game_xgoals`)
- Create: `packages/ptb-core/src/ptb/core/warehouse/loaders/asa.py`
- Modify: `packages/ptb-core/src/ptb/core/warehouse/loaders/__init__.py` (import asa)
- Create: `tests/fixtures/asa_nwsl_teams_sample.json`, `asa_nwsl_players_sample.json`, `asa_nwsl_games_sample.json`, `asa_nwsl_games_xgoals_sample.json`
- Test: `tests/test_asa_loader.py` (reference + game portion)

**Interfaces:**
- Consumes: `LOADERS` from `warehouse.load`, envelopes from Task 5
- Produces: `load_asa(con, payload: dict, archive_key: str) -> int` routed on `payload["resource"]`, registered `LOADERS["asa"]`

- [ ] **Step 1: Extend the schema**

Append to `packages/ptb-core/src/ptb/core/warehouse/schema.sql`. First the new competition seeds (add to the existing `dim_competition` INSERT by adding a second statement immediately after it):

```sql
INSERT OR REPLACE INTO dim_competition (competition_id, country, name, tier, gender) VALUES
    ('NWSL', 'USA', 'National Womens Soccer League', 1, 'W'),
    ('MLS',  'USA', 'Major League Soccer',           1, 'M'),
    ('USLC', 'USA', 'USL Championship',              2, 'M'),
    ('USL1', 'USA', 'USL League One',                3, 'M');

-- -------------------------------------------------------------------- asa

CREATE TABLE IF NOT EXISTS src_asa_team (
    league            TEXT NOT NULL,
    team_id           TEXT NOT NULL,
    team_name         TEXT NOT NULL,
    team_short_name   TEXT,
    team_abbreviation TEXT,
    PRIMARY KEY (league, team_id)
);

CREATE TABLE IF NOT EXISTS src_asa_player (
    league                   TEXT NOT NULL,
    player_id                TEXT NOT NULL,
    season                   TEXT NOT NULL,
    player_name              TEXT,
    birth_date               DATE,
    nationality              TEXT,
    primary_general_position TEXT,
    PRIMARY KEY (league, player_id, season)
);

CREATE TABLE IF NOT EXISTS src_asa_game (
    game_id      TEXT PRIMARY KEY,
    league       TEXT NOT NULL,
    season       TEXT NOT NULL,
    kickoff_utc  TIMESTAMP,
    home_team_id TEXT NOT NULL,
    away_team_id TEXT NOT NULL,
    home_score   INTEGER,
    away_score   INTEGER,
    matchday     INTEGER,
    status       TEXT,
    archive_key  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS src_asa_game_xgoals (
    game_id            TEXT PRIMARY KEY,
    league             TEXT NOT NULL,
    season             TEXT NOT NULL,
    home_team_id       TEXT,
    away_team_id       TEXT,
    home_goals         INTEGER,
    away_goals         INTEGER,
    home_team_xgoals   DOUBLE,
    away_team_xgoals   DOUBLE,
    home_player_xgoals DOUBLE,
    away_player_xgoals DOUBLE,
    home_xpoints       DOUBLE,
    away_xpoints       DOUBLE,
    archive_key        TEXT NOT NULL
);
```

Keep every comment free of semicolons — the schema loader splits on `;`.

- [ ] **Step 2: Create the golden fixtures**

`tests/fixtures/asa_nwsl_teams_sample.json`:

```json
{
  "source": "asa", "endpoint": "nwsl/teams", "league": "nwsl", "resource": "teams",
  "season": null, "url": "https://app.americansocceranalysis.com/api/v1/nwsl/teams",
  "fetched_at": "2026-08-03T12:00:00Z",
  "data": [
    {"team_id": "T_POR", "team_name": "Portland Thorns FC", "team_short_name": "Portland", "team_abbreviation": "POR"},
    {"team_id": "T_SEA", "team_name": "Seattle Reign FC", "team_short_name": "Seattle", "team_abbreviation": "RGN"}
  ]
}
```

`tests/fixtures/asa_nwsl_players_sample.json`:

```json
{
  "source": "asa", "endpoint": "nwsl/players", "league": "nwsl", "resource": "players",
  "season": null, "url": "https://app.americansocceranalysis.com/api/v1/nwsl/players",
  "fetched_at": "2026-08-03T12:00:00Z",
  "data": [
    {"player_id": "P1", "player_name": "Sophia Smith", "birth_date": "2000-08-10",
     "nationality": "USA", "primary_general_position": "ST", "season_name": "2024"},
    {"player_id": "P1", "player_name": "Sophia Smith", "birth_date": "2000-08-10",
     "nationality": "USA", "primary_general_position": "ST", "season_name": "2023"}
  ]
}
```

`tests/fixtures/asa_nwsl_games_sample.json` (note the late UTC kickoff — 01:00 UTC is the prior evening in Pacific time):

```json
{
  "source": "asa", "endpoint": "nwsl/games", "league": "nwsl", "resource": "games",
  "season": "2024", "url": "https://app.americansocceranalysis.com/api/v1/nwsl/games",
  "fetched_at": "2026-08-03T12:00:00Z",
  "data": [
    {"game_id": "G1", "date_time_utc": "2024-06-15 02:30:00 UTC", "home_score": 2,
     "away_score": 1, "home_team_id": "T_POR", "away_team_id": "T_SEA",
     "matchday": 10, "status": "FullTime", "season_name": "2024"}
  ]
}
```

`tests/fixtures/asa_nwsl_games_xgoals_sample.json`:

```json
{
  "source": "asa", "endpoint": "nwsl/games-xgoals", "league": "nwsl", "resource": "games/xgoals",
  "season": "2024", "url": "https://app.americansocceranalysis.com/api/v1/nwsl/games/xgoals",
  "fetched_at": "2026-08-03T12:00:00Z",
  "data": [
    {"game_id": "G1", "home_team_id": "T_POR", "away_team_id": "T_SEA",
     "home_goals": 2, "away_goals": 1, "home_team_xgoals": 1.8, "away_team_xgoals": 0.9,
     "home_player_xgoals": 1.7, "away_player_xgoals": 0.8,
     "home_xpoints": 2.1, "away_xpoints": 0.6}
  ]
}
```

- [ ] **Step 3: Write the failing test**

`tests/test_asa_loader.py`:

```python
import datetime as dt
import json
from pathlib import Path

import pytest

from ptb.core.warehouse import db
from ptb.core.warehouse.loaders import asa as loader

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def con(tmp_path):
    connection = db.connect(tmp_path / "test.duckdb")
    yield connection
    connection.close()


def _load(con, name):
    payload = json.loads((FIX / name).read_text(encoding="utf-8"))
    return loader.load_asa(con, payload, "asa/" + name)


def test_asa_is_registered():
    from ptb.core.warehouse.load import LOADERS
    assert "asa" in LOADERS


def test_teams_loader(con):
    assert _load(con, "asa_nwsl_teams_sample.json") == 2
    row = con.execute(
        "SELECT team_name, team_abbreviation FROM src_asa_team "
        "WHERE league = 'nwsl' AND team_id = 'T_POR'"
    ).fetchone()
    assert row == ("Portland Thorns FC", "POR")


def test_players_loader_one_row_per_season(con):
    assert _load(con, "asa_nwsl_players_sample.json") == 2
    row = con.execute(
        "SELECT player_name, birth_date FROM src_asa_player "
        "WHERE player_id = 'P1' AND season = '2024'"
    ).fetchone()
    assert row == ("Sophia Smith", dt.date(2000, 8, 10))


def test_games_loader_parses_utc_kickoff(con):
    assert _load(con, "asa_nwsl_games_sample.json") == 1
    row = con.execute(
        "SELECT league, season, kickoff_utc, home_team_id, away_team_id, "
        "       home_score, away_score, status FROM src_asa_game WHERE game_id = 'G1'"
    ).fetchone()
    assert row[0] == "nwsl"
    assert row[1] == "2024"
    assert row[2] == dt.datetime(2024, 6, 15, 2, 30)
    assert (row[3], row[4]) == ("T_POR", "T_SEA")
    assert (row[5], row[6]) == (2, 1)
    assert row[7] == "FullTime"


def test_game_xgoals_loader(con):
    assert _load(con, "asa_nwsl_games_xgoals_sample.json") == 1
    row = con.execute(
        "SELECT home_team_xgoals, away_team_xgoals, home_xpoints "
        "FROM src_asa_game_xgoals WHERE game_id = 'G1'"
    ).fetchone()
    assert row[0] == pytest.approx(1.8)
    assert row[1] == pytest.approx(0.9)
    assert row[2] == pytest.approx(2.1)


def test_loaders_are_idempotent(con):
    for name in ("asa_nwsl_teams_sample.json", "asa_nwsl_games_sample.json"):
        _load(con, name)
        _load(con, name)
    assert con.execute("SELECT count(*) FROM src_asa_team").fetchone()[0] == 2
    assert con.execute("SELECT count(*) FROM src_asa_game").fetchone()[0] == 1
```

- [ ] **Step 4: Run test to verify it fails**

Run: `uv run pytest tests/test_asa_loader.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ptb.core.warehouse.loaders.asa'`

- [ ] **Step 5: Write the loader**

`packages/ptb-core/src/ptb/core/warehouse/loaders/asa.py`:

```python
"""ASA payloads -> src_asa_* tables, routed on payload["resource"].

Reference resources (teams, players) and per-game and per-player-season stat
tables each have their own shape. Player stat tables (goals-added, xgoals,
xpass) are handled in asa_players.py; this module covers reference and game
resources.
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


def _date(value: Any) -> Optional[dt.date]:
    if not value:
        return None
    try:
        return dt.datetime.strptime(str(value).strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _utc(value: Any) -> Optional[dt.datetime]:
    """'2024-06-15 02:30:00 UTC' -> naive datetime (already UTC)."""
    if not value:
        return None
    text = str(value).replace(" UTC", "").strip()
    try:
        return dt.datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _load_teams(con, payload, archive_key) -> int:
    league = payload["league"]
    rows = [[league, r["team_id"], r.get("team_name"), r.get("team_short_name"),
             r.get("team_abbreviation")] for r in payload["data"] if r.get("team_id")]
    if rows:
        con.executemany(
            "INSERT OR REPLACE INTO src_asa_team "
            "(league, team_id, team_name, team_short_name, team_abbreviation) "
            "VALUES (?, ?, ?, ?, ?)", rows)
    return len(rows)


def _load_players(con, payload, archive_key) -> int:
    league = payload["league"]
    rows = [[league, r["player_id"], str(r.get("season_name")), r.get("player_name"),
             _date(r.get("birth_date")), r.get("nationality"),
             r.get("primary_general_position")]
            for r in payload["data"] if r.get("player_id") and r.get("season_name")]
    if rows:
        con.executemany(
            "INSERT OR REPLACE INTO src_asa_player "
            "(league, player_id, season, player_name, birth_date, nationality, "
            " primary_general_position) VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    return len(rows)


def _load_games(con, payload, archive_key) -> int:
    league, season = payload["league"], payload["season"]
    rows = [[r["game_id"], league, season, _utc(r.get("date_time_utc")),
             r.get("home_team_id"), r.get("away_team_id"),
             _i(r.get("home_score")), _i(r.get("away_score")),
             _i(r.get("matchday")), r.get("status"), archive_key]
            for r in payload["data"]
            if r.get("game_id") and r.get("home_team_id") and r.get("away_team_id")]
    if rows:
        con.executemany(
            "INSERT OR REPLACE INTO src_asa_game "
            "(game_id, league, season, kickoff_utc, home_team_id, away_team_id, "
            " home_score, away_score, matchday, status, archive_key) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    return len(rows)


def _load_game_xgoals(con, payload, archive_key) -> int:
    league, season = payload["league"], payload["season"]
    rows = [[r["game_id"], league, season, r.get("home_team_id"), r.get("away_team_id"),
             _i(r.get("home_goals")), _i(r.get("away_goals")),
             _f(r.get("home_team_xgoals")), _f(r.get("away_team_xgoals")),
             _f(r.get("home_player_xgoals")), _f(r.get("away_player_xgoals")),
             _f(r.get("home_xpoints")), _f(r.get("away_xpoints")), archive_key]
            for r in payload["data"] if r.get("game_id")]
    if rows:
        con.executemany(
            "INSERT OR REPLACE INTO src_asa_game_xgoals "
            "(game_id, league, season, home_team_id, away_team_id, home_goals, "
            " away_goals, home_team_xgoals, away_team_xgoals, home_player_xgoals, "
            " away_player_xgoals, home_xpoints, away_xpoints, archive_key) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    return len(rows)


_ROUTES = {
    "teams": _load_teams,
    "players": _load_players,
    "games": _load_games,
    "games/xgoals": _load_game_xgoals,
}


def load_asa(con: duckdb.DuckDBPyConnection, payload: dict, archive_key: str) -> int:
    resource = payload.get("resource")
    handler = _ROUTES.get(resource)
    if handler is None:
        from .asa_players import load_player_stats, PLAYER_ROUTES
        if resource in PLAYER_ROUTES:
            return load_player_stats(con, payload, archive_key)
        log.warning("no asa loader for resource %r in %s", resource, archive_key)
        return 0
    return handler(con, payload, archive_key)


LOADERS["asa"] = load_asa
```

`packages/ptb-core/src/ptb/core/warehouse/loaders/__init__.py` — append:

```python
from . import asa  # noqa: F401  -- registers the asa loader
```

Note: the `asa_players` import is inside the fallback branch, so this task's tests (reference + games) pass before Task 7 creates that module.

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_asa_loader.py -v`
Expected: 6 passed

- [ ] **Step 7: Commit**

```bash
git add packages/ptb-core/src/ptb/core/warehouse/ tests/fixtures/asa_nwsl_*.json tests/test_asa_loader.py
git commit -m "Load ASA teams, players and games into the warehouse"
```

---

### Task 7: ASA player-stat loaders

**Files:**
- Modify: `packages/ptb-core/src/ptb/core/warehouse/schema.sql` (player stat tables)
- Create: `packages/ptb-core/src/ptb/core/warehouse/loaders/asa_players.py`
- Create: `tests/fixtures/asa_nwsl_goals_added_sample.json`, `asa_nwsl_xgoals_sample.json`, `asa_nwsl_xpass_sample.json`
- Test: `tests/test_asa_loader.py` (add player-stat tests)

**Interfaces:**
- Consumes: `payload["resource"]` in `{"players/goals-added","players/xgoals","players/xpass"}`
- Produces: `load_player_stats(con, payload, archive_key) -> int`, `PLAYER_ROUTES: dict[str, callable]`

- [ ] **Step 1: Extend the schema**

Append to `packages/ptb-core/src/ptb/core/warehouse/schema.sql`:

```sql
CREATE TABLE IF NOT EXISTS src_asa_player_goals_added (
    league                TEXT NOT NULL,
    season                TEXT NOT NULL,
    player_id             TEXT NOT NULL,
    team_id               TEXT,
    general_position      TEXT,
    minutes_played        INTEGER,
    action_type           TEXT NOT NULL,
    goals_added_raw       DOUBLE,
    goals_added_above_avg DOUBLE,
    count_actions         INTEGER,
    archive_key           TEXT NOT NULL,
    PRIMARY KEY (league, season, player_id, action_type)
);

CREATE TABLE IF NOT EXISTS src_asa_player_xgoals (
    league                     TEXT NOT NULL,
    season                     TEXT NOT NULL,
    player_id                  TEXT NOT NULL,
    team_id                    TEXT,
    general_position           TEXT,
    minutes_played             INTEGER,
    shots                      INTEGER,
    shots_on_target            INTEGER,
    goals                      INTEGER,
    xgoals                     DOUBLE,
    xplace                     DOUBLE,
    key_passes                 INTEGER,
    primary_assists            INTEGER,
    xassists                   DOUBLE,
    goals_plus_primary_assists INTEGER,
    xgoals_plus_xassists       DOUBLE,
    points_added               DOUBLE,
    xpoints_added              DOUBLE,
    archive_key                TEXT NOT NULL,
    PRIMARY KEY (league, season, player_id)
);

CREATE TABLE IF NOT EXISTS src_asa_player_xpass (
    league                              TEXT NOT NULL,
    season                              TEXT NOT NULL,
    player_id                           TEXT NOT NULL,
    team_id                             TEXT,
    general_position                    TEXT,
    minutes_played                      INTEGER,
    attempted_passes                    INTEGER,
    pass_completion_percentage          DOUBLE,
    xpass_completion_percentage         DOUBLE,
    passes_completed_over_expected      DOUBLE,
    passes_completed_over_expected_p100 DOUBLE,
    avg_distance_yds                    DOUBLE,
    avg_vertical_distance_yds           DOUBLE,
    share_team_touches                  DOUBLE,
    count_games                         INTEGER,
    archive_key                         TEXT NOT NULL,
    PRIMARY KEY (league, season, player_id)
);
```

- [ ] **Step 2: Create the golden fixtures**

`tests/fixtures/asa_nwsl_goals_added_sample.json`:

```json
{
  "source": "asa", "endpoint": "nwsl/players-goals-added", "league": "nwsl",
  "resource": "players/goals-added", "season": "2024",
  "url": "https://app.americansocceranalysis.com/api/v1/nwsl/players/goals-added",
  "fetched_at": "2026-08-03T12:00:00Z",
  "data": [
    {"player_id": "P1", "team_id": "T_POR", "general_position": "ST", "minutes_played": 1800,
     "data": [
       {"action_type": "Shooting", "goals_added_raw": 2.5, "goals_added_above_avg": 1.1, "count_actions": 60},
       {"action_type": "Receiving", "goals_added_raw": 0.8, "goals_added_above_avg": 0.3, "count_actions": 400}
     ]}
  ]
}
```

`tests/fixtures/asa_nwsl_xgoals_sample.json`:

```json
{
  "source": "asa", "endpoint": "nwsl/players-xgoals", "league": "nwsl",
  "resource": "players/xgoals", "season": "2024",
  "url": "https://app.americansocceranalysis.com/api/v1/nwsl/players/xgoals",
  "fetched_at": "2026-08-03T12:00:00Z",
  "data": [
    {"player_id": "P1", "team_id": "T_POR", "general_position": "ST", "minutes_played": 1800,
     "shots": 70, "shots_on_target": 30, "goals": 14, "xgoals": 12.6, "xplace": 1.2,
     "key_passes": 25, "primary_assists": 5, "xassists": 4.1,
     "goals_plus_primary_assists": 19, "xgoals_plus_xassists": 16.7,
     "points_added": 3.4, "xpoints_added": 2.9}
  ]
}
```

`tests/fixtures/asa_nwsl_xpass_sample.json`:

```json
{
  "source": "asa", "endpoint": "nwsl/players-xpass", "league": "nwsl",
  "resource": "players/xpass", "season": "2024",
  "url": "https://app.americansocceranalysis.com/api/v1/nwsl/players/xpass",
  "fetched_at": "2026-08-03T12:00:00Z",
  "data": [
    {"player_id": "P1", "team_id": "T_POR", "general_position": "ST", "minutes_played": 1800,
     "attempted_passes": 900, "pass_completion_percentage": 0.78,
     "xpass_completion_percentage": 0.74, "passes_completed_over_expected": 36.0,
     "passes_completed_over_expected_p100": 4.0, "avg_distance_yds": 15.2,
     "avg_vertical_distance_yds": 6.1, "share_team_touches": 0.09, "count_games": 22}
  ]
}
```

- [ ] **Step 3: Add the failing tests**

Append to `tests/test_asa_loader.py`:

```python
def test_goals_added_explodes_by_action_type(con):
    assert _load(con, "asa_nwsl_goals_added_sample.json") == 2
    rows = con.execute(
        "SELECT action_type, goals_added_raw, count_actions "
        "FROM src_asa_player_goals_added WHERE player_id = 'P1' ORDER BY action_type"
    ).fetchall()
    assert rows == [("Receiving", pytest.approx(0.8), 400),
                    ("Shooting", pytest.approx(2.5), 60)]


def test_player_xgoals_loader(con):
    assert _load(con, "asa_nwsl_xgoals_sample.json") == 1
    row = con.execute(
        "SELECT goals, xgoals, primary_assists, points_added "
        "FROM src_asa_player_xgoals WHERE player_id = 'P1' AND season = '2024'"
    ).fetchone()
    assert row[0] == 14
    assert row[1] == pytest.approx(12.6)
    assert row[2] == 5
    assert row[3] == pytest.approx(3.4)


def test_player_xpass_loader(con):
    assert _load(con, "asa_nwsl_xpass_sample.json") == 1
    row = con.execute(
        "SELECT attempted_passes, xpass_completion_percentage, count_games "
        "FROM src_asa_player_xpass WHERE player_id = 'P1' AND season = '2024'"
    ).fetchone()
    assert row[0] == 900
    assert row[1] == pytest.approx(0.74)
    assert row[2] == 22


def test_player_stat_loaders_are_idempotent(con):
    for name in ("asa_nwsl_goals_added_sample.json", "asa_nwsl_xgoals_sample.json"):
        _load(con, name)
        _load(con, name)
    assert con.execute("SELECT count(*) FROM src_asa_player_goals_added").fetchone()[0] == 2
    assert con.execute("SELECT count(*) FROM src_asa_player_xgoals").fetchone()[0] == 1
```

- [ ] **Step 4: Run test to verify it fails**

Run: `uv run pytest tests/test_asa_loader.py -k "goals_added or xgoals or xpass" -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ptb.core.warehouse.loaders.asa_players'`

- [ ] **Step 5: Write the player-stat loader**

`packages/ptb-core/src/ptb/core/warehouse/loaders/asa_players.py`:

```python
"""ASA per-player-season stat payloads -> src_asa_player_* tables.

goals-added nests a per-action-type array, exploded to one row per action.
xgoals and xpass are flat, one row per player-season.
"""
from __future__ import annotations

from typing import Any, Optional

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


def _load_goals_added(con, payload, archive_key) -> int:
    league, season = payload["league"], payload["season"]
    rows = []
    for r in payload["data"]:
        pid = r.get("player_id")
        if not pid:
            continue
        for action in r.get("data", []):
            rows.append([
                league, season, pid, r.get("team_id"), r.get("general_position"),
                _i(r.get("minutes_played")), action.get("action_type"),
                _f(action.get("goals_added_raw")), _f(action.get("goals_added_above_avg")),
                _i(action.get("count_actions")), archive_key,
            ])
    if rows:
        con.executemany(
            "INSERT OR REPLACE INTO src_asa_player_goals_added "
            "(league, season, player_id, team_id, general_position, minutes_played, "
            " action_type, goals_added_raw, goals_added_above_avg, count_actions, archive_key) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    return len(rows)


def _load_xgoals(con, payload, archive_key) -> int:
    league, season = payload["league"], payload["season"]
    rows = [[league, season, r["player_id"], r.get("team_id"), r.get("general_position"),
             _i(r.get("minutes_played")), _i(r.get("shots")), _i(r.get("shots_on_target")),
             _i(r.get("goals")), _f(r.get("xgoals")), _f(r.get("xplace")),
             _i(r.get("key_passes")), _i(r.get("primary_assists")), _f(r.get("xassists")),
             _i(r.get("goals_plus_primary_assists")), _f(r.get("xgoals_plus_xassists")),
             _f(r.get("points_added")), _f(r.get("xpoints_added")), archive_key]
            for r in payload["data"] if r.get("player_id")]
    if rows:
        con.executemany(
            "INSERT OR REPLACE INTO src_asa_player_xgoals "
            "(league, season, player_id, team_id, general_position, minutes_played, shots, "
            " shots_on_target, goals, xgoals, xplace, key_passes, primary_assists, xassists, "
            " goals_plus_primary_assists, xgoals_plus_xassists, points_added, xpoints_added, "
            " archive_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    return len(rows)


def _load_xpass(con, payload, archive_key) -> int:
    league, season = payload["league"], payload["season"]
    rows = [[league, season, r["player_id"], r.get("team_id"), r.get("general_position"),
             _i(r.get("minutes_played")), _i(r.get("attempted_passes")),
             _f(r.get("pass_completion_percentage")), _f(r.get("xpass_completion_percentage")),
             _f(r.get("passes_completed_over_expected")),
             _f(r.get("passes_completed_over_expected_p100")),
             _f(r.get("avg_distance_yds")), _f(r.get("avg_vertical_distance_yds")),
             _f(r.get("share_team_touches")), _i(r.get("count_games")), archive_key]
            for r in payload["data"] if r.get("player_id")]
    if rows:
        con.executemany(
            "INSERT OR REPLACE INTO src_asa_player_xpass "
            "(league, season, player_id, team_id, general_position, minutes_played, "
            " attempted_passes, pass_completion_percentage, xpass_completion_percentage, "
            " passes_completed_over_expected, passes_completed_over_expected_p100, "
            " avg_distance_yds, avg_vertical_distance_yds, share_team_touches, count_games, "
            " archive_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    return len(rows)


PLAYER_ROUTES = {
    "players/goals-added": _load_goals_added,
    "players/xgoals": _load_xgoals,
    "players/xpass": _load_xpass,
}


def load_player_stats(con: duckdb.DuckDBPyConnection, payload: dict, archive_key: str) -> int:
    return PLAYER_ROUTES[payload["resource"]](con, payload, archive_key)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_asa_loader.py -v`
Expected: 10 passed

- [ ] **Step 7: Commit**

```bash
git add packages/ptb-core/src/ptb/core/warehouse/ tests/fixtures/asa_nwsl_goals_added_sample.json tests/fixtures/asa_nwsl_xgoals_sample.json tests/fixtures/asa_nwsl_xpass_sample.json tests/test_asa_loader.py
git commit -m "Load ASA player goals-added, xG and xPass into the warehouse"
```

---

### Task 8: ASA match resolution and NWSL late kickoff

**Files:**
- Modify: `packages/ptb-core/src/ptb/core/identity/matches.py` (add `resolve_asa`)
- Modify: `packages/ptb-core/src/ptb/core/warehouse/load.py` (call it in `rebuild`)
- Test: `tests/test_identity_understat_asa.py` (add ASA tests)

**Interfaces:**
- Consumes: `resolve_match`, `src_asa_game` joined to `src_asa_team`
- Produces: `resolve_asa(con) -> int`

- [ ] **Step 1: Add the failing tests**

Append to `tests/test_identity_understat_asa.py`:

```python
def _insert_asa_team(con, league, team_id, name):
    con.execute(
        "INSERT OR REPLACE INTO src_asa_team (league, team_id, team_name) VALUES (?, ?, ?)",
        [league, team_id, name])


def _insert_asa_game(con, gid, league, season, kickoff, home_id, away_id):
    con.execute(
        "INSERT INTO src_asa_game "
        "(game_id, league, season, kickoff_utc, home_team_id, away_team_id, archive_key) "
        "VALUES (?, ?, ?, ?, ?, ?, 'k')",
        [gid, league, season, kickoff, home_id, away_id])


def test_resolve_asa_maps_games_via_team_names(con):
    _insert_asa_team(con, "nwsl", "T_POR", "Portland Thorns FC")
    _insert_asa_team(con, "nwsl", "T_SEA", "Seattle Reign FC")
    _insert_asa_game(con, "G1", "nwsl", "2024",
                     dt.datetime(2024, 6, 15, 2, 30), "T_POR", "T_SEA")
    resolved = matches.resolve_asa(con)
    assert resolved == 1
    row = con.execute(
        "SELECT m.competition, t.canonical_name FROM dim_match m "
        "JOIN dim_team t ON t.team_id = m.home_team_id"
    ).fetchone()
    assert row == ("NWSL", "Portland Thorns FC")


def test_asa_late_kickoff_resolves_within_window(con):
    """An NWSL game at 02:30 UTC (prior evening Pacific) and a source reporting
    the local Saturday date must resolve to one dim_match."""
    _insert_asa_team(con, "nwsl", "T_POR", "Portland Thorns FC")
    _insert_asa_team(con, "nwsl", "T_SEA", "Seattle Reign FC")
    _insert_asa_game(con, "G_UTC", "nwsl", "2024",
                     dt.datetime(2024, 6, 15, 2, 30), "T_POR", "T_SEA")
    matches.resolve_asa(con)

    # a hypothetical second source reporting the local-date kickoff
    other = matches.resolve_match(
        con, source="fotmob", source_match_id="fm-1", competition="NWSL",
        season="2024", kickoff=dt.datetime(2024, 6, 14, 19, 30),
        home_team="Portland Thorns FC", away_team="Seattle Reign FC")

    assert con.execute("SELECT count(*) FROM dim_match").fetchone()[0] == 1
    assert other is not None


def test_resolve_asa_is_idempotent(con):
    _insert_asa_team(con, "nwsl", "T_POR", "Portland Thorns FC")
    _insert_asa_team(con, "nwsl", "T_SEA", "Seattle Reign FC")
    _insert_asa_game(con, "G1", "nwsl", "2024",
                     dt.datetime(2024, 6, 15, 2, 30), "T_POR", "T_SEA")
    matches.resolve_asa(con)
    matches.resolve_asa(con)
    assert con.execute("SELECT count(*) FROM dim_match").fetchone()[0] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_identity_understat_asa.py -k asa -v`
Expected: FAIL — `AttributeError: module 'ptb.core.identity.matches' has no attribute 'resolve_asa'`

- [ ] **Step 3: Write `resolve_asa`**

Append to `packages/ptb-core/src/ptb/core/identity/matches.py`:

```python
_ASA_LEAGUE_TO_COMPETITION = {
    "nwsl": "NWSL", "mls": "MLS", "uslc": "USLC", "usl1": "USL1",
}


def resolve_asa(con: duckdb.DuckDBPyConnection) -> int:
    """Resolve every ASA game into dim_match. Idempotent.

    ASA games reference teams by hashed id, so the game is joined to
    src_asa_team for the names resolve_match needs. NWSL kickoffs are true UTC,
    so late kickoffs crossing the local date boundary resolve through the
    +/-36h window against any other source reporting the local date.
    """
    rows = con.execute(
        "SELECT g.game_id, g.league, g.season, g.kickoff_utc, ht.team_name, at.team_name "
        "FROM src_asa_game g "
        "JOIN src_asa_team ht ON ht.league = g.league AND ht.team_id = g.home_team_id "
        "JOIN src_asa_team at ON at.league = g.league AND at.team_id = g.away_team_id "
        "WHERE g.kickoff_utc IS NOT NULL "
        "ORDER BY g.kickoff_utc, g.game_id"
    ).fetchall()

    resolved = 0
    for game_id, league, season, kickoff, home, away in rows:
        competition = _ASA_LEAGUE_TO_COMPETITION.get(league)
        if competition is None:
            continue
        if resolve_match(
            con, source="asa", source_match_id=str(game_id),
            competition=competition, season=season, kickoff=kickoff,
            home_team=home, away_team=away,
        ) is not None:
            resolved += 1
    return resolved
```

- [ ] **Step 4: Wire it into `rebuild`**

In `packages/ptb-core/src/ptb/core/warehouse/load.py`, extend the identity block so it ends:

```python
    if written.get("understat"):
        resolved = identity_matches.resolve_understat(con)
        log.info("resolved %d understat matches into dim_match", resolved)

    if written.get("asa"):
        resolved = identity_matches.resolve_asa(con)
        log.info("resolved %d asa games into dim_match", resolved)

    return written
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_identity_understat_asa.py -v`
Expected: 6 passed

- [ ] **Step 6: Commit**

```bash
git add packages/ptb-core/src/ptb/core/ tests/test_identity_understat_asa.py
git commit -m "Resolve ASA games into dim_match, including NWSL late kickoffs"
```

---

### Task 9: Generalize `ptb coverage` across all sources

**Files:**
- Modify: `packages/ptb-core/src/ptb/core/cli.py` (`_cmd_coverage`)
- Test: `tests/test_coverage.py`

**Interfaces:**
- Consumes: `src_footballdata_match`, `src_understat_match`, `src_asa_game`
- Produces: an updated `_cmd_coverage` returning a source × competition × season grid

- [ ] **Step 1: Write the failing test**

`tests/test_coverage.py`:

```python
import datetime as dt
import io
from contextlib import redirect_stdout

import pytest

from ptb.core.warehouse import db
from ptb.core.cli import _cmd_coverage
from ptb.core import config


class _Args:
    pass


@pytest.fixture
def warehouse(tmp_path, monkeypatch):
    db_path = tmp_path / "ptb.duckdb"
    monkeypatch.setattr(config, "DB_PATH", db_path)
    con = db.connect(db_path)
    con.execute(
        "INSERT INTO src_footballdata_match "
        "(competition, season, match_date, home_team, away_team, archive_key) "
        "VALUES ('E0', '2024/25', DATE '2024-08-17', 'Arsenal', 'Wolves', 'k')"
    )
    con.execute(
        "INSERT INTO src_understat_match "
        "(understat_match_id, competition, season, kickoff, home_team, away_team, archive_key) "
        "VALUES ('1001', 'E0', '2024/25', TIMESTAMP '2024-08-17 15:00:00', 'Arsenal', 'Wolves', 'k')"
    )
    con.execute(
        "INSERT INTO src_asa_game "
        "(game_id, league, season, kickoff_utc, home_team_id, away_team_id, archive_key) "
        "VALUES ('G1', 'nwsl', '2024', TIMESTAMP '2024-06-15 02:30:00', 'T_POR', 'T_SEA', 'k')"
    )
    con.close()
    return db_path


def test_coverage_reports_all_three_sources(warehouse):
    out = io.StringIO()
    with redirect_stdout(out):
        rc = _cmd_coverage(_Args())
    assert rc == 0
    text = out.getvalue()
    assert "footballdata" in text
    assert "understat" in text
    assert "asa" in text
    assert "NWSL" in text  # asa league mapped to competition
    assert "E0" in text


def test_coverage_reports_empty_warehouse(tmp_path, monkeypatch):
    db_path = tmp_path / "ptb.duckdb"
    monkeypatch.setattr(config, "DB_PATH", db_path)
    db.connect(db_path).close()
    out = io.StringIO()
    with redirect_stdout(out):
        rc = _cmd_coverage(_Args())
    assert rc == 1
    assert "empty" in out.getvalue().lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_coverage.py -v`
Expected: FAIL — only `footballdata` present (understat/asa not in the old query)

- [ ] **Step 3: Rewrite `_cmd_coverage`**

Replace `_cmd_coverage` in `packages/ptb-core/src/ptb/core/cli.py` with:

```python
def _cmd_coverage(args) -> int:
    from . import config
    from .warehouse import db

    if not config.DB_PATH.is_file():
        print("no warehouse yet -- run `ptb ingest` then `ptb rebuild`")
        return 1

    con = db.connect(read_only=True)
    try:
        rows = con.execute(
            "SELECT 'footballdata' AS source, competition, season, count(*) AS matches "
            "  FROM src_footballdata_match GROUP BY 1, 2, 3 "
            "UNION ALL "
            "SELECT 'understat', competition, season, count(*) "
            "  FROM src_understat_match GROUP BY 1, 2, 3 "
            "UNION ALL "
            "SELECT 'asa', "
            "  CASE league WHEN 'nwsl' THEN 'NWSL' WHEN 'mls' THEN 'MLS' "
            "              WHEN 'uslc' THEN 'USLC' WHEN 'usl1' THEN 'USL1' ELSE league END, "
            "  season, count(*) "
            "  FROM src_asa_game GROUP BY 1, 2, 3 "
            "ORDER BY 1, 2, 3"
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_coverage.py -v`
Expected: 2 passed

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add packages/ptb-core/src/ptb/core/cli.py tests/test_coverage.py
git commit -m "Report coverage across all sources, not just football-data"
```

---

## Verification

After Task 9, this sequence should work end to end (small slices to keep the live run quick):

```bash
uv run pytest

# Understat: one league-season (fetches the league payload + ~380 match shot payloads)
uv run ptb ingest understat --season 2024/25
# football-data for the same season so cross-source resolution has something to match
uv run ptb ingest footballdata --competition E0,SP1,I1,D1,F1 --season 2024/25
# ASA: NWSL one season
uv run ptb ingest asa --season 2024

uv run ptb rebuild
uv run ptb coverage
```

Then confirm the two headline properties:

**1. Cross-source match identity.** An Understat Big-5 match and its football-data counterpart share one `dim_match`:

```bash
uv run python -c "
from ptb.core.warehouse import db
con = db.connect(read_only=True)
n = con.execute('''
  SELECT count(*) FROM (
    SELECT match_id FROM map_match_source WHERE source IN ('understat','footballdata')
    GROUP BY match_id HAVING count(DISTINCT source) = 2
  )
''').fetchone()[0]
print('matches carrying both understat and football-data:', n)
"
```

Expected: a large number (most E0/SP1/I1/D1/F1 2024/25 fixtures), proving the cross-source join works.

**2. Disposability.** Deleting the warehouse and rebuilding offline reproduces the identical coverage grid:

```bash
rm data/ptb.duckdb && uv run ptb rebuild && uv run ptb coverage
```

The grid must be identical, with no network access during `rebuild`. Incremental ingest also means a second `ptb ingest understat --season 2024/25` fetches only the league payload (all match shots already archived), while `--refetch` re-pulls everything.

## Deliberate deviations from the spec

**MLS/USL included in ASA.** As recorded in the spec, this widens the Plan 1
coverage decision (Big 5 + NWSL + WSL). Justified by near-zero marginal cost
(identical API) and future WAR value. USL is two ASA leagues, so it appears as
two competitions: `USLC` (Championship) and `USL1` (League One).

**NWSL/MLS/USL team aliases are not pre-seeded.** Plan 1's alias file exists to
make *cross-source* names conform. Understat's Big-5 spellings need that (they
must meet football-data's), so a few are added. The US leagues currently have
only one source (ASA), so `resolve_team` creating dim_team rows straight from
ASA names is correct and sufficient. Aliases for them are added when a second
US-league source (FotMob/StatsBomb) arrives, per YAGNI.

## What comes next

- **Plan 3 sources (a later spec):** FotMob shot maps and StatsBomb open data —
  the remaining two v1 sources, both currently blocked on `soccer-gar` code not
  present on this machine.
- **Plan 3 conformed layer:** the precedence table, `v_match` / `v_shot` /
  `v_player_match`, player identity (`map_player_source` with derived confidence
  plus a committed override file), and `ptb status` / `ptb verify`. Understat and
  ASA now give it real multi-source data to reconcile.
