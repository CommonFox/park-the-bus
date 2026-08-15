# park-the-bus

A personal monorepo for soccer analytics. `ptb-core` collects data from
several free/scraped sources into a local DuckDB warehouse; analytics
projects (FPL research, a match-entertainment rating, a WAR-style metric,
NWSL analysis) are built on top of it.

Nothing here is a service. It's a CLI you run on your own machine, and the
warehouse is a disposable local file you can always throw away and rebuild.

## Architecture: bronze → silver → gold

```
data/raw/{source}/{endpoint}/{label}__{stamp}.json.gz   bronze  gitignored, append-only, source of truth
        ↓  ptb rebuild (no network)
src_{source}_{entity}                                    silver  one table per source per entity, never merged
        ↓  identity resolution
dim_*, map_*_source                                       silver  conformed entities + source id bridges
        ↓  (not built yet)
v_*                                                        gold    conformed views, explicit source precedence
```

**Bronze — the archive (`data/raw/`).** Every fetch writes a verbatim,
gzipped JSON payload, keyed by `{source}/{endpoint}/{label}__{timestamp}`,
and payloads are never mutated. This is the actual source of truth, not the
warehouse — a parser bug is a re-parse, not a re-fetch. Gitignored; lives
only on disk. [`archive.py`](packages/ptb-core/src/ptb/core/archive.py) is
addressed purely by string key — only its private `_path` knows a key maps
to a file, so object storage can drop in later.

**Silver — `src_*` tables.** One table per source per entity
(`src_footballdata_match`, `src_fpl_player_gw`, `src_asa_player_xgoals`, …),
typed but otherwise source-faithful. Nothing is reconciled here: if two
sources disagree, both rows survive. On top of this sits identity
resolution — `dim_team`, `dim_match`, `dim_player` and `map_*_source` bridge
tables that give each real-world team/match/player one surrogate id across
sources (team aliases + diacritic folding; matches resolved on a ±36h
kickoff window, not exact date equality, so a late-Pacific-kickoff NWSL game
and a UTC-reporting source still land on the same match; players matched
into an FPL-anchored spine).

**Gold — conformed views.** Not built yet;
[`gold/`](packages/ptb-core/src/ptb/core/gold/) is scaffolded and empty. The
plan is `v_*` views plus a `(measure, source, priority)` precedence table, so
e.g. "xg" resolves Understat → FotMob without that choice being hardcoded in
a loader. This is the layer downstream projects (FPL, GAR, WAR) are meant to
query. The concrete first view identified so far is `v_match_odds` —
de-vigging football-data's settled odds (`src_footballdata_odds`) and
DraftKings' forward odds into probabilities with source precedence, which is
what an FPL fixture-difficulty solver would read.

The rule that makes the whole thing rebuildable from nothing: **`ptb
ingest` only ever writes to the archive; `ptb rebuild` only ever reads from
it.** Nothing does both. Delete `data/` entirely and `ptb ingest all && ptb
rebuild` reproduces it.

### Layout

`ptb-core` is plain functions and modules — no classes outside a couple of
`NamedTuple` records and the exception types. Each source owns its whole
pipeline in one module plus one schema file, so adding a source means adding
files in `silver/` and one line to `SOURCES`:

```
packages/ptb-core/src/ptb/core/
├── archive.py       bronze: write/read/keys, addressed by string key
├── warehouse.py     connect, apply schema, replay the archive
├── config.py        all paths env-overridable
├── cli.py           the `ptb` command
├── silver/
│   ├── __init__.py      SOURCES: name -> module
│   ├── <source>.py      ingest() + load() + resolve_matches()/resolve_players()
│   ├── <source>.sql     that source's tables
│   ├── names.py         normalization + fuzzy comparison
│   ├── teams.py  matches.py  players.py    conformed dimensions (+ .sql, .yaml)
│   └── coerce.py        defensive numeric parsing shared by every loader
└── gold/            conformed views (empty)
```

`warehouse.apply_schema` globs `silver/*.sql` in sorted order — no file
references another's tables, so a new source needs no registration there.

Full design rationale: [`docs/superpowers/specs/2026-08-02-universal-soccer-data-layer-design.md`](docs/superpowers/specs/2026-08-02-universal-soccer-data-layer-design.md).

## Setup

```bash
uv sync
```

Requires Python ≥3.12. `uv` manages a workspace at the repo root; the actual
package is `packages/ptb-core`.

## Usage

```bash
ptb ingest footballdata --competition E0,SP1,D1,I1,F1 --season 2025/26
ptb ingest all --season 2025/26        # every registered source
ptb rebuild                            # replay archive → warehouse, no network
ptb coverage                           # source × competition × season grid
```

`ptb --version`, `-v`/`--verbose` for debug logging. `ptb status` and `ptb
verify` are specced but not implemented yet.

## Sources

| Source | Registry name | Covers | Granularity |
|---|---|---|---|
| football-data.co.uk | `footballdata` | Big-5 leagues + 2nd divisions (E0/E1, SP1/SP2, D1/D2, I1/I2, F1/F2) | Match results, shots, cards in `src_footballdata_match`; Bet365/Pinnacle/Max/Avg 1X2, O/U 2.5, Asian handicap (opening + closing) in `src_footballdata_odds` |
| Understat | `understat` | EPL, La Liga, Bundesliga, Serie A, Ligue 1 | Match + shot-level xG |
| ASA (`itscalledsoccer`) | `asa` | NWSL, MLS, USLC, USL1 | Teams, players, games, g+, xG, xPass |
| FotMob | `fotmob` | Premier League, Championship | Season stat-tables (player + team) |
| FPL API | `fpl` | Premier League, current season | Bootstrap, fixtures, per-gameweek player history |
| vaastav (Fantasy-Premier-League GitHub dataset) | `vaastav` | Premier League, historical seasons | Backfilled per-gameweek player history |
| DraftKings | `draftkings` | Premier League | Forward moneyline + totals odds |

Women's competitions (NWSL, and eventually WSL) rest entirely on FotMob +
ASA + StatsBomb open data, since football-data.co.uk and Understat cover
men's football only. StatsBomb is specced but not yet built.

## Current status

What's real on `master` right now:

- Archive, warehouse schema, CLI, and all 7 sources above — ingest and
  loaders both, 243 passing tests.
- Team and match identity resolution (`dim_team`, `dim_match`) across all
  sources that report matches.
- **Player identity, wired into `ptb rebuild` and `ptb coverage`.**
  `dim_player`, `map_player_source` and `unresolved_player` are populated
  on every rebuild: an FPL-code-anchored spine, Understat and FotMob
  matched into it, plus committed manual corrections in
  `silver/player_overrides.yaml`. Validation against `../fpl-app`'s
  existing player map is the open piece — see CLAUDE.md for exactly what's
  left.
- **A real local `data/`** — full ingest across all 7 sources, including a
  10-season Understat backfill across the Big 5. A full `ptb rebuild`
  against it takes ~75–85 minutes, identity resolution included.

What hasn't started:

- The gold layer (`v_*` views + precedence table) — explicitly the
  next foundational piece; see "Roadmap" below.
- StatsBomb open data, WSL coverage.
- `ptb status`, `ptb verify`.
- Porting analytics from `../fpl-app` and `../soccer-gar` (below).

## Roadmap: the four projects this layer feeds

Per the founding spec, four analytics projects are meant to sit on top of
this warehouse, each its own `packages/ptb-*` package depending on
`ptb-core`:

- **FPL** — Fantasy Premier League research, ported from
  [`../fpl-app`](../fpl-app) (formerly `sacked-in-the-morning`). That repo
  already has the ingestion this monorepo has been rebuilding, *plus* the
  parts that haven't been ported yet: points modelling, a squad optimiser,
  backtesting, fixture-difficulty ratings. Porting is blocked on the gold
  layer existing, since the analytics code is meant to query conformed
  views, not raw `src_*` tables.
- **GAR** — a single-number match-entertainment score, ported from
  [`../soccer-gar`](../soccer-gar).
- **WAR** — a wins-above-replacement equivalent for soccer. Not yet scoped;
  the spec defers scoping until `ptb coverage` shows what data is actually
  available.
- **NWSL** — analysis of the NWSL. Not yet scoped.

## Docs

- `docs/superpowers/specs/` — one design doc per unit of work, decisions
  and rationale.
- `docs/superpowers/plans/` — the task-by-task implementation plan derived
  from each spec.

Note: plan-file checkboxes are never checked off after implementation, even
for work that's fully merged — they're not a reliable progress signal. Git
history and branch state are.

## Testing

```bash
uv run pytest
```

Golden-payload fixtures for every source live in `tests/fixtures/`, so
loaders are testable without network access.
