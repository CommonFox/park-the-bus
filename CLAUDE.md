# CLAUDE.md

Guidance for Claude Code sessions working in this repository.

## What this is

Personal soccer analytics monorepo. `ptb-core` (`packages/ptb-core/`) is a
bronze/silver/gold data layer — raw archive → source-faithful tables →
conformed views — that future analytics packages (`ptb-fpl`, `ptb-gar`,
`ptb-war`, `ptb-nwsl`) will sit on top of. See [README.md](README.md) for
the full architecture, source list, and current build status — don't
duplicate that here, read it first.

## The one invariant that matters most

`ptb ingest` writes only to the archive (`data/raw/`, gitignored). `ptb
rebuild` reads only from the archive and replays it into DuckDB. **Never
make a change that has ingest touch the warehouse or rebuild touch the
network.** This is what makes the warehouse fully disposable — deleting
`data/` and rebuilding must always reproduce it exactly. Every loader must
stay idempotent (re-running on an already-loaded archive key is a no-op via
`meta_archive_loaded`).

## Layout

```
packages/ptb-core/src/ptb/core/
├── archive.py       bronze: write/read/keys/exists, addressed by string key
│                     (only its private _path maps a key to a file)
├── warehouse.py     connect + apply_schema (globs silver/*.sql) + rebuild (replay)
├── silver/          one module per source, each owning its whole pipeline
│   ├── __init__.py   SOURCES: name -> module, and source()/source_names()
│   ├── <source>.py   NAME, ingest(), load(), and its resolve_* identity hooks
│   ├── <source>.sql  that source's own tables
│   ├── names.py      normalization + fuzzy name comparison (pure, no db)
│   ├── teams.py / matches.py / players.py   conformed dimensions + resolution
│   │                  (each with a sibling .sql; teams and players also .yaml)
│   ├── meta.sql / competitions.sql          bookkeeping + seeded dimension
│   └── coerce.py     as_float/as_int, shared by every loader
├── gold/            conformed v_* views — scaffolded, empty (see below)
├── config.py        all paths env-overridable (tests point at tmp dirs)
└── cli.py           `ptb` command: ingest / rebuild / coverage
tests/                flat, test_<topic>.py, offline via tests/fixtures/*.json
                      conftest.py points config.RAW_DIR at tmp_path (autouse)
docs/superpowers/
├── specs/            one design doc per unit of work — decisions + rationale
└── plans/            task-by-task plan derived from each spec
```

**No classes.** This is deliberate and was the point of the 2026-08-15
refactor: plain modules and functions throughout, the only classes being
two `NamedTuple` records in `silver/players.py` and the exception types
(`UnknownSourceError`, `NotPublishedError`, `Blocked`). Don't reintroduce a
`Source` base class, a registry decorator, or an archive/backend object —
those all existed and were removed. Dispatch is a plain dict
(`silver.SOURCES`) and optional behaviour is `getattr(module, "hook", None)`.

Adding a source means: `silver/<name>.py` exposing `NAME`, `ingest(...)` and
`load(con, payload, archive_key)`; `silver/<name>.sql` with its `src_*`
tables; one entry in `_MODULES` in `silver/__init__.py`; a golden fixture in
`tests/fixtures/`. Schema application and replay pick it up from there. Two
things do *not*: `cli.py`'s `coverage` command is hand-written SQL and needs
a manual `UNION ALL`, and match/player identity needs a `resolve_matches` /
`resolve_players` function in the module if the source reports either.

## Workflow convention in this repo

Work follows spec → plan → implementation: a design doc lands in
`docs/superpowers/specs/`, a task-by-task plan derived from it lands in
`docs/superpowers/plans/`, then implementation happens (often via a
worktree under `.claude/worktrees/<name>/` on its own branch). **Plan-file
checkboxes are never checked off, even for fully merged work** — every plan
in this repo currently shows 0 checked regardless of actual status. Don't
trust them as a progress signal; check `git log`/branch state instead.

## Known gaps as of 2026-08-15 (verify before relying on this — it will drift)

- **The whole package was restructured on 2026-08-15** into the bronze /
  `silver/` / `gold/` layout above, with every class removed. Anything
  written before that date — specs, plans, PR descriptions, old commit
  messages — describes `sources/`, `warehouse/loaders/`, `identity/`,
  `RawArchive` and `@register_source`, none of which exist any more. The
  mapping is: `sources/<n>.py` + `warehouse/loaders/<n>.py` →
  `silver/<n>.py`; `identity/*` → `silver/*`; `warehouse/schema.sql` →
  per-source `silver/*.sql`; `archive/` → `archive.py`; `warehouse/db.py` +
  `load.py` → `warehouse.py`.

- **Player identity: Tasks 9–11 done, Task 12 in progress.** `dim_player`,
  `map_player_source`, `unresolved_player`, `fotmob.resolve_players`,
  `silver/player_overrides.yaml`, and the top-level `players.resolve_players`
  are all on master and wired into `ptb rebuild`/`coverage`
  (`4b381fd`..`7b04f80`, `772fb95`). A real local warehouse now exists (all
  sources ingested, full 10-season Understat backfill across the Big 5) and
  `dim_player` populates correctly — this is no longer inert.

  What's still open on Task 12 (validation against `../fpl-app`'s player
  map, [`docs/superpowers/plans/2026-08-03-player-identity.md`](docs/superpowers/plans/2026-08-03-player-identity.md)):
  running the matcher against real historical data for the first time (it
  had only ever run against synthetic same-season test fixtures) surfaced
  three real precision/recall bugs in the matching logic now in
  `silver/names.py` / `silver/players.py`, all found, fixed, and
  unit-tested:
  1. Fuzzy comparison of truncated surname variants caused false collisions
     between unrelated players with similar short surnames (`Ings` vs
     `Mings` scored 0.933) — `931225d`.
  2. `resolve_source_players` decided a player's match using only their
     earliest `(competition, season)` appearance; a player whose
     Understat/FotMob history predates their move to England (candidates
     only ever exist for competition `E0`) was permanently `created` even
     once a real, matchable FPL candidate existed in a later season —
     `7b04f80`.
  3. A truncated-form collision between two *different* full names (`Andre
     Gray` / `Archie Gray` both reduce to `"a gray"`) was being treated as
     certain rather than coincidental — `772fb95`.

  Each fix measurably increased matched players in real rebuilds (0 → 1,191
  → 1,625 → 2,865 across the session), but **the final rebuild + validation
  cycle after fix 3 was never run** (each cycle takes ~80 minutes against
  the full archive) — `scripts/compare_player_map.py`'s coverage bars
  (67%/77%) have not been re-checked against all three fixes together. That
  re-run is the concrete next step before Task 12 can be marked done. Two
  bugs in the validation script itself were also found and fixed along the
  way (`ATTACH` needs a literal path, not a bound parameter; `USING SAMPLE`
  applies before `WHERE`, not after) — see `8e241f4`.

  Also uncommitted-and-now-folded-into-the-refactor: `src_understat_player`
  (Understat's own league-season roster, so zero-shot players — most
  goalkeepers — enter resolution at all) and a swapped-order name variant
  for players whose registered name is East Asian (`Endo Wataru` vs `Wataru
  Endo`). Both are tested but have never been through a full rebuild either.
- **No gold layer.** `gold/` exists but holds only a docstring: no `v_*`
  views, no `(measure, source, priority)` precedence table. This is the
  actual next foundational piece — it's what the FPL/GAR ports are blocked
  on. [PR #6](../../pull/6)'s description
  names the concrete first view: `v_match_odds`, de-vigging
  `src_footballdata_odds` (settled) + `src_draftkings_odds` (forward) into
  probabilities with source precedence. See also
  [`docs/superpowers/specs/2026-08-08-fpl-replatform-design.md`](docs/superpowers/specs/2026-08-08-fpl-replatform-design.md),
  which depends on this and on Task 12 landing first.
- **Local `data/` now exists and is substantial.** Full ingest across all 7
  sources, including a complete 10-season (2016/17–2025/26) Understat
  backfill across the Big 5. `ptb rebuild` currently takes ~75-85 minutes
  end to end against this archive (identity resolution included) — plan
  accordingly before kicking one off interactively.
- `ptb status` / `ptb verify` are specced, not implemented.
- Branch/worktree hygiene is currently clean: no stale worktrees, no dead
  local branches. If a new one shows up while investigating something,
  check `git merge-base --is-ancestor <branch> master` before assuming a
  similarly-named branch is the one that actually got merged — this repo
  has already had one case (`feat/foundation-and-first-source` vs. the
  real `feat/foundation-first-source`) where two branches differed by one
  word and only one of them mattered.

## Sibling repos

- `../fpl-app` (remote: `sacked-in-the-morning`) — the more mature FPL
  project. Its ingestion side has largely been re-implemented here
  (footballdata, understat, fotmob, fpl, vaastav, draftkings all exist in
  both). Its analytics side (points model, squad optimiser, backtesting,
  FDR, ticker) has **not** been ported — that's the actual FPL work still
  ahead, and it's gated on the gold layer existing here first.
- `../soccer-gar` — the GAR (match entertainment rating) project, not yet
  ported.

## Testing

`uv run pytest` from repo root. Offline by default — every source has a
committed golden-payload fixture in `tests/fixtures/`. Idempotency and
full-rebuild-determinism are both tested explicitly since the whole design
depends on them.
