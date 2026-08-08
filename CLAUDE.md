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
├── archive/       RawArchive + backend (local now, object storage later)
├── sources/        one module per provider + registry.py (name -> class lookup)
├── identity/       team/match/player resolution (player resolution merged but not
│                    wired into rebuild/coverage yet, see below)
├── warehouse/       schema.sql, db.py (connect + apply schema), load.py (replay),
│                    loaders/ (one per source, registers into load.LOADERS)
├── config.py        all paths env-overridable (tests point at tmp dirs)
└── cli.py           `ptb` command: ingest / rebuild / coverage
tests/                flat, test_<topic>.py, offline via tests/fixtures/*.json
docs/superpowers/
├── specs/            one design doc per unit of work — decisions + rationale
└── plans/            task-by-task plan derived from each spec
```

Adding a source means: a `sources/<name>.py` implementing the `Source`
protocol ([`sources/base.py`](packages/ptb-core/src/ptb/core/sources/base.py)),
registered via `@register_source`; a `warehouse/loaders/<name>.py`
registered into `LOADERS`; `src_*` tables in `schema.sql`; a golden fixture
in `tests/fixtures/`. `cli.py`'s `coverage` command is hand-written SQL, not
derived from the registry — a new source needs a manual `UNION ALL` added
there too.

## Workflow convention in this repo

Work follows spec → plan → implementation: a design doc lands in
`docs/superpowers/specs/`, a task-by-task plan derived from it lands in
`docs/superpowers/plans/`, then implementation happens (often via a
worktree under `.claude/worktrees/<name>/` on its own branch). **Plan-file
checkboxes are never checked off, even for fully merged work** — every plan
in this repo currently shows 0 checked regardless of actual status. Don't
trust them as a progress signal; check `git log`/branch state instead.

## Known gaps as of 2026-08-08 (verify before relying on this — it will drift)

- **Player identity is merged but inert.** `dim_player`,
  `map_player_source`, `unresolved_player`, name/team/DOB matching, the
  FPL-anchored spine (`build_fpl_spine`), and Understat resolution
  (`resolve_understat_players`) are all on master, all tested (192 passing
  total). But `warehouse/load.py`'s `rebuild()` never calls either
  resolver, and `cli.py`'s `coverage` never reports `unresolved_player` —
  so `ptb rebuild` today leaves `dim_player` permanently empty. Per
  [`docs/superpowers/plans/2026-08-03-player-identity.md`](docs/superpowers/plans/2026-08-03-player-identity.md)
  (landed via [PR #7](../../pull/7), merged as Tasks 1–8 without finishing
  the plan): still missing are Task 9 (FotMob player resolution), Task 10
  (`player_overrides.yaml` + a top-level resolver that calls both source
  resolvers and applies overrides), Task 11 (wire that top-level resolver
  into `rebuild`/`coverage` — this is the specific gap making the merged
  code inert), and Task 12 (validation against `../fpl-app`'s existing
  player map).
- **No gold layer.** No `v_*` views, no `(measure, source, priority)`
  precedence table. This is the actual next foundational piece — it's what
  the FPL/GAR ports are blocked on. [PR #6](../../pull/6)'s description
  names the concrete first view: `v_match_odds`, de-vigging
  `src_footballdata_odds` (settled) + `src_draftkings_odds` (forward) into
  probabilities with source precedence.
- **No local `data/`.** A fresh checkout has never had a full `ptb ingest`
  run against it, so `ptb coverage` has nothing to show until that happens.
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
