# football-data Odds Enrichment — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture the curated football-data odds set (Bet365 / Pinnacle / Max / Avg 1X2 opening + closing, Over/Under 2.5, Asian handicap) into a new `src_footballdata_odds` table, replayed from the existing archive with no new fetch.

**Architecture:** A schema addition and a loader extension only. `load_footballdata` already parses each CSV row into `src_footballdata_match`; it is extended to also emit a `src_footballdata_odds` row from the same parsed CSV row. The three odds columns Plan 1 kept on `src_footballdata_match` (`odds_home/draw/away`) move into the new table as `b365_h/d/a`. `ptb rebuild` repopulates both tables from archived payloads.

**Tech Stack:** Python 3.12, `uv` workspace, DuckDB, `pytest`.

**Spec:** `docs/superpowers/specs/2026-08-03-footballdata-odds-enrichment-design.md`

## Global Constraints

- Python `>=3.12`. Unchanged.
- No new fetch or source change. This is a loader + schema change only.
- Every loader is idempotent (`INSERT OR REPLACE`); rebuild is deterministic.
- Columns absent in older-era files parse as NULL (read by name, tolerate absence).
- `src_footballdata_odds` shares `src_footballdata_match`'s natural key so they join directly.
- A `src_footballdata_odds` row is written only when at least one 1X2 opening price is present, so odds-less eras add nothing.

## Confirmed CSV column names (probed live 2026-08-03, E0 2024/25, 120 cols)

| Warehouse column | CSV column | | Warehouse column | CSV column |
|---|---|---|---|---|
| `b365_h/d/a` | `B365H/B365D/B365A` | | `over25_b365` / `under25_b365` | `B365>2.5` / `B365<2.5` |
| `ps_h/d/a` | `PSH/PSD/PSA` | | `over25_avg` / `under25_avg` | `Avg>2.5` / `Avg<2.5` |
| `max_h/d/a` | `MaxH/MaxD/MaxA` | | `over25_avgc` / `under25_avgc` | `AvgC>2.5` / `AvgC<2.5` |
| `avg_h/d/a` | `AvgH/AvgD/AvgA` | | `ah_line` | `AHh` |
| `b365c_h/d/a` | `B365CH/B365CD/B365CA` | | `ah_home_avg` / `ah_away_avg` | `AvgAHH` / `AvgAHA` |
| `psc_h/d/a` | `PSCH/PSCD/PSCA` | | `ah_home_ps` / `ah_away_ps` | `PAHH` / `PAHA` |
| `avgc_h/d/a` | `AvgCH/AvgCD/AvgCA` | | `ahc_line` | `AHCh` |
| | | | `ahc_home_avg` / `ahc_away_avg` | `AvgCAHH` / `AvgCAHA` |
| | | | `ahc_home_ps` / `ahc_away_ps` | `PCAHH` / `PCAHA` |

---

## File Structure

| File | Responsibility |
|---|---|
| `.../ptb/core/warehouse/schema.sql` | Add `src_footballdata_odds`; drop `odds_*` from `src_footballdata_match` |
| `.../ptb/core/warehouse/loaders/footballdata.py` | Emit a `src_footballdata_odds` row per priced match |
| `tests/fixtures/footballdata_e0_odds_sample.json` | Golden payload with the rich odds columns |
| `tests/test_footballdata_odds.py` | Odds-table tests |
| `tests/test_footballdata_loader.py` | Update `test_load_maps_columns` (odds moved out) |

---

### Task 1: football-data odds table and loader

**Files:**
- Modify: `packages/ptb-core/src/ptb/core/warehouse/schema.sql`
- Modify: `packages/ptb-core/src/ptb/core/warehouse/loaders/footballdata.py`
- Create: `tests/fixtures/footballdata_e0_odds_sample.json`
- Create: `tests/test_footballdata_odds.py`
- Modify: `tests/test_footballdata_loader.py`

**Interfaces:**
- Consumes: `payload["csv"]` (unchanged football-data envelope)
- Produces: `src_footballdata_odds` rows; `load_footballdata` still returns the match-row count

- [ ] **Step 1: Create the golden odds fixture**

`tests/fixtures/footballdata_e0_odds_sample.json` (one fully-priced match; one with empty odds fields, which must produce no odds row):

```json
{
  "source": "footballdata",
  "competition": "E0",
  "season": "2024/25",
  "url": "https://www.football-data.co.uk/mmz4281/2425/E0.csv",
  "fetched_at": "2026-08-03T10:15:00Z",
  "csv": "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,B365H,B365D,B365A,PSH,PSD,PSA,MaxH,MaxD,MaxA,AvgH,AvgD,AvgA,B365CH,B365CD,B365CA,PSCH,PSCD,PSCA,AvgCH,AvgCD,AvgCA,B365>2.5,B365<2.5,Avg>2.5,Avg<2.5,AvgC>2.5,AvgC<2.5,AHh,B365AHH,B365AHA,PAHH,PAHA,AvgAHH,AvgAHA,AHCh,AvgCAHH,AvgCAHA,PCAHH,PCAHA\nE0,17/08/2024,Arsenal,Wolves,2,0,H,1.25,6.50,11.00,1.26,6.60,12.50,1.28,6.80,13.00,1.25,6.40,11.50,1.22,7.00,13.00,1.23,7.10,13.50,1.21,6.90,12.80,1.80,2.00,1.83,1.98,1.85,1.97,-1.5,1.95,1.92,1.96,1.94,1.93,1.90,-1.75,1.99,1.88,2.01,1.86\nE0,17/08/2024,Everton,Brighton,0,3,A,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,\n"
}
```

- [ ] **Step 2: Write the failing tests**

`tests/test_footballdata_odds.py`:

```python
import datetime as dt
import json
from pathlib import Path

import pytest

from ptb.core.warehouse import db
from ptb.core.warehouse.loaders import footballdata as loader

FIXTURE = Path(__file__).parent / "fixtures" / "footballdata_e0_odds_sample.json"


@pytest.fixture
def con(tmp_path):
    connection = db.connect(tmp_path / "test.duckdb")
    yield connection
    connection.close()


@pytest.fixture
def payload():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_priced_match_gets_an_odds_row(con, payload):
    loader.load_footballdata(con, payload, "k")
    # two matches loaded, but only the priced one gets an odds row
    assert con.execute("SELECT count(*) FROM src_footballdata_match").fetchone()[0] == 2
    assert con.execute("SELECT count(*) FROM src_footballdata_odds").fetchone()[0] == 1
    assert con.execute(
        "SELECT count(*) FROM src_footballdata_odds WHERE home_team = 'Everton'"
    ).fetchone()[0] == 0


def test_odds_columns_map(con, payload):
    loader.load_footballdata(con, payload, "k")
    row = con.execute(
        "SELECT competition, season, match_date, b365_h, ps_h, max_h, avg_h, "
        "       b365c_h, psc_h, avgc_h, over25_b365, under25_b365, over25_avg, "
        "       over25_avgc, ah_line, ah_home_avg, ah_home_ps, ahc_line, ahc_home_avg "
        "FROM src_footballdata_odds WHERE home_team = 'Arsenal' AND away_team = 'Wolves'"
    ).fetchone()
    assert row[0] == "E0"
    assert row[1] == "2024/25"
    assert row[2] == dt.date(2024, 8, 17)
    assert row[3] == pytest.approx(1.25)   # b365_h
    assert row[4] == pytest.approx(1.26)   # ps_h
    assert row[5] == pytest.approx(1.28)   # max_h
    assert row[6] == pytest.approx(1.25)   # avg_h (AvgH)
    assert row[7] == pytest.approx(1.22)   # b365c_h (B365CH)
    assert row[8] == pytest.approx(1.23)   # psc_h (PSCH)
    assert row[10] == pytest.approx(1.80)  # over25_b365 (B365>2.5)
    assert row[11] == pytest.approx(2.00)  # under25_b365 (B365<2.5)
    assert row[12] == pytest.approx(1.83)  # over25_avg (Avg>2.5)
    assert row[13] == pytest.approx(1.85)  # over25_avgc (AvgC>2.5)
    assert row[14] == pytest.approx(-1.5)  # ah_line (AHh)
    assert row[15] == pytest.approx(1.93)  # ah_home_avg (AvgAHH)
    assert row[16] == pytest.approx(1.96)  # ah_home_ps (PAHH)
    assert row[17] == pytest.approx(-1.75) # ahc_line (AHCh)
    assert row[18] == pytest.approx(1.99)  # ahc_home_avg (AvgCAHH)


def test_odds_no_longer_on_the_match_table(con, payload):
    loader.load_footballdata(con, payload, "k")
    columns = {row[1] for row in con.execute("PRAGMA table_info('src_footballdata_match')").fetchall()}
    assert "odds_home" not in columns
    assert "odds_draw" not in columns
    assert "odds_away" not in columns


def test_oddsless_era_writes_no_odds_row(con):
    payload = {
        "source": "footballdata", "competition": "E0", "season": "1993/94",
        "url": "u", "fetched_at": "2026-08-03T10:15:00Z",
        "csv": "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR\n"
               "E0,14/08/93,Arsenal,Coventry,0,3,A\n",
    }
    rows = loader.load_footballdata(con, payload, "k")
    assert rows == 1
    assert con.execute("SELECT count(*) FROM src_footballdata_match").fetchone()[0] == 1
    assert con.execute("SELECT count(*) FROM src_footballdata_odds").fetchone()[0] == 0


def test_odds_loader_is_idempotent(con, payload):
    loader.load_footballdata(con, payload, "k")
    loader.load_footballdata(con, payload, "k")
    assert con.execute("SELECT count(*) FROM src_footballdata_odds").fetchone()[0] == 1
```

Also update `tests/test_footballdata_loader.py`'s `test_load_maps_columns`: remove `odds_home` from the `SELECT` and drop its assertion, since that column moves out of `src_footballdata_match`. Replace:

```python
def test_load_maps_columns_correctly(con, payload):
    loader.load_footballdata(con, payload, "some/key.json.gz")
    row = con.execute(
        "SELECT season, kickoff_time, home_goals, away_goals, result, "
        "       home_shots, away_shots, home_corners, referee "
        "FROM src_footballdata_match WHERE home_team = 'Arsenal'"
    ).fetchone()

    assert row[0] == "2024/25"
    assert row[1] == dt.time(15, 0)
    assert (row[2], row[3], row[4]) == (2, 0, "H")
    assert (row[5], row[6]) == (20, 6)
    assert row[7] == 10
    assert row[8] == "S Hooper"
```

(The old version also selected `odds_home` and asserted `row[9] == pytest.approx(1.25)`; both are removed.)

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_footballdata_odds.py -v`
Expected: FAIL — `Catalog Error: Table ... src_footballdata_odds does not exist`

- [ ] **Step 4: Extend the schema**

In `packages/ptb-core/src/ptb/core/warehouse/schema.sql`, first remove the three odds columns from `src_footballdata_match` (delete these lines from that table):

```sql
    odds_home     DOUBLE,
    odds_draw     DOUBLE,
    odds_away     DOUBLE,
```

Then append the new table:

```sql
-- ------------------------------------------------------- football-data odds
-- Curated bookmaker odds, split out of src_footballdata_match. One row per
-- match, sharing its natural key. Columns absent in older eras load NULL.

CREATE TABLE IF NOT EXISTS src_footballdata_odds (
    competition   TEXT NOT NULL,
    season        TEXT NOT NULL,
    match_date    DATE NOT NULL,
    home_team     TEXT NOT NULL,
    away_team     TEXT NOT NULL,
    b365_h DOUBLE, b365_d DOUBLE, b365_a DOUBLE,
    ps_h   DOUBLE, ps_d   DOUBLE, ps_a   DOUBLE,
    max_h  DOUBLE, max_d  DOUBLE, max_a  DOUBLE,
    avg_h  DOUBLE, avg_d  DOUBLE, avg_a  DOUBLE,
    b365c_h DOUBLE, b365c_d DOUBLE, b365c_a DOUBLE,
    psc_h   DOUBLE, psc_d   DOUBLE, psc_a   DOUBLE,
    avgc_h  DOUBLE, avgc_d  DOUBLE, avgc_a  DOUBLE,
    over25_b365 DOUBLE, under25_b365 DOUBLE,
    over25_avg  DOUBLE, under25_avg  DOUBLE,
    over25_avgc DOUBLE, under25_avgc DOUBLE,
    ah_line      DOUBLE, ah_home_avg  DOUBLE, ah_away_avg  DOUBLE,
                         ah_home_ps   DOUBLE, ah_away_ps   DOUBLE,
    ahc_line     DOUBLE, ahc_home_avg DOUBLE, ahc_away_avg DOUBLE,
                         ahc_home_ps  DOUBLE, ahc_away_ps  DOUBLE,
    archive_key   TEXT NOT NULL,
    PRIMARY KEY (competition, season, match_date, home_team, away_team)
);
```

Keep every comment free of semicolons — the schema loader splits on `;`.

- [ ] **Step 5: Rewrite the loader**

Replace `packages/ptb-core/src/ptb/core/warehouse/loaders/footballdata.py` with:

```python
"""football-data.co.uk CSV -> src_footballdata_match and src_footballdata_odds.

Reads strictly by column name. Column availability varies by era -- 1993/94 has
no match statistics and no odds, 2000/01 has shots, 2024/25 has 120 columns
including a large odds block -- so every optional column is fetched defensively
and absence is normal, not an error.

The curated odds set is split into its own table (src_footballdata_odds), keyed
the same as the match row. A match with no 1X2 opening price gets no odds row.
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

# CSV column -> match-table column
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

_MATCH_COLUMNS = [
    "competition", "season", "match_date", "kickoff_time", "home_team", "away_team",
    "home_goals", "away_goals", "result", "ht_home_goals", "ht_away_goals", "ht_result",
    "home_shots", "away_shots", "home_sot", "away_sot", "home_fouls", "away_fouls",
    "home_corners", "away_corners", "home_yellows", "away_yellows",
    "home_reds", "away_reds", "referee", "archive_key",
]

# CSV column -> odds-table column. Absence is normal in older eras.
_ODDS_MAP = {
    "B365H": "b365_h", "B365D": "b365_d", "B365A": "b365_a",
    "PSH": "ps_h", "PSD": "ps_d", "PSA": "ps_a",
    "MaxH": "max_h", "MaxD": "max_d", "MaxA": "max_a",
    "AvgH": "avg_h", "AvgD": "avg_d", "AvgA": "avg_a",
    "B365CH": "b365c_h", "B365CD": "b365c_d", "B365CA": "b365c_a",
    "PSCH": "psc_h", "PSCD": "psc_d", "PSCA": "psc_a",
    "AvgCH": "avgc_h", "AvgCD": "avgc_d", "AvgCA": "avgc_a",
    "B365>2.5": "over25_b365", "B365<2.5": "under25_b365",
    "Avg>2.5": "over25_avg", "Avg<2.5": "under25_avg",
    "AvgC>2.5": "over25_avgc", "AvgC<2.5": "under25_avgc",
    "AHh": "ah_line", "AvgAHH": "ah_home_avg", "AvgAHA": "ah_away_avg",
    "PAHH": "ah_home_ps", "PAHA": "ah_away_ps",
    "AHCh": "ahc_line", "AvgCAHH": "ahc_home_avg", "AvgCAHA": "ahc_away_avg",
    "PCAHH": "ahc_home_ps", "PCAHA": "ahc_away_ps",
}
_ODDS_COLUMNS = (
    ["competition", "season", "match_date", "home_team", "away_team"]
    + list(_ODDS_MAP.values())
    + ["archive_key"]
)
# A 1X2 opening price from any of these means the row is worth an odds row.
_ODDS_PRESENT_KEYS = ("b365_h", "ps_h", "max_h", "avg_h")


def parse_date(text: Optional[str]) -> Optional[dt.date]:
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
    """One CSV row -> one match dict, or None if it is not a real match."""
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
    return parsed


def parse_odds(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """One CSV row -> one odds dict, or None if it carries no 1X2 opening price."""
    odds = {target: _as_float(_get(row, source)) for source, target in _ODDS_MAP.items()}
    if not any(odds.get(k) is not None for k in _ODDS_PRESENT_KEYS):
        return None
    return odds


def load_footballdata(con: duckdb.DuckDBPyConnection, payload: dict, archive_key: str) -> int:
    competition = payload["competition"]
    season = payload["season"]

    reader = csv.DictReader(io.StringIO(payload["csv"]))
    match_rows = []
    odds_rows = []
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
        match_rows.append([parsed.get(column) for column in _MATCH_COLUMNS])

        odds = parse_odds(raw_row)
        if odds is not None:
            odds.update({
                "competition": competition, "season": season,
                "match_date": parsed["match_date"], "home_team": parsed["home_team"],
                "away_team": parsed["away_team"], "archive_key": archive_key,
            })
            odds_rows.append([odds.get(column) for column in _ODDS_COLUMNS])

    if match_rows:
        con.executemany(
            "INSERT OR REPLACE INTO src_footballdata_match ({}) VALUES ({})".format(
                ", ".join(_MATCH_COLUMNS), ", ".join("?" * len(_MATCH_COLUMNS))),
            match_rows)
    if odds_rows:
        con.executemany(
            "INSERT OR REPLACE INTO src_footballdata_odds ({}) VALUES ({})".format(
                ", ".join(_ODDS_COLUMNS), ", ".join("?" * len(_ODDS_COLUMNS))),
            odds_rows)
    log.debug("loaded %d matches, %d odds rows from %s %s",
              len(match_rows), len(odds_rows), competition, season)
    return len(match_rows)


LOADERS["footballdata"] = load_footballdata
```

The changes from Plan 1: `_ODDS` and the odds columns are gone from `_MATCH_COLUMNS` (now `_MATCH_COLUMNS`, previously `_COLUMNS`); `parse_row` no longer reads odds; `parse_odds` and the odds insert are new.

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_footballdata_odds.py tests/test_footballdata_loader.py -v`
Expected: all pass (5 new odds tests, plus the existing loader tests with the updated `test_load_maps_columns`)

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass

- [ ] **Step 8: Commit**

```bash
git add packages/ptb-core/src/ptb/core/warehouse/ tests/fixtures/footballdata_e0_odds_sample.json tests/test_footballdata_odds.py tests/test_footballdata_loader.py
git commit -m "Split a curated football-data odds set into src_footballdata_odds"
```

---

## Verification

After Task 1, replay real archived data end to end:

```bash
uv run pytest

uv run ptb ingest footballdata --competition E0,SP1,I1,D1,F1 --season 2024/25
uv run ptb rebuild
uv run python -c "
from ptb.core.warehouse import db
con = db.connect(read_only=True)
print('match rows:', con.execute('SELECT count(*) FROM src_footballdata_match').fetchone()[0])
print('odds rows :', con.execute('SELECT count(*) FROM src_footballdata_odds').fetchone()[0])
r = con.execute('''
  SELECT home_team, away_team, b365_h, ps_h, avg_h, over25_avg, ah_line
  FROM src_footballdata_odds WHERE competition='E0' AND b365_h IS NOT NULL LIMIT 1
''').fetchone()
print('sample odds:', r)
"
```

Expected: match and odds counts both near 1,900 for the five leagues (every recent-season match is priced), and a sample row with populated Bet365, Pinnacle, average, over/under and Asian-handicap prices.

Then disposability:

```bash
rm data/ptb.duckdb && uv run ptb rebuild
```

Match and odds counts must be identical, with no network access during `rebuild`.

## What comes next

- The **conformed layer**: a `v_match_odds` view that de-vigs both football-data
  (settled) and DraftKings (forward) prices into probabilities with source
  precedence, which the FPL `fdr` solver reads.
- The **FPL modelling port** -- every source it reads is now in the warehouse.
