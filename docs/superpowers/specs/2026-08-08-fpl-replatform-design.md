# FPL Re-platform onto ptb-core — Design

**Date:** 2026-08-08
**Status:** Proposed — needs review before a plan is written
**Scope:** How `../fpl-app`'s derived modeling and optimisation code moves onto
`ptb-core`'s warehouse. The modeling and optimisation *logic* itself
(minutes/points/ratings math, the MIP formulation) is out of scope — this
spec is about where it queries from, not what it computes.

## Purpose

`../fpl-app` (remote: `sacked-in-the-morning`) is a mature FPL research
system: 13,464 lines of application code, 4,411 lines of tests, a walk-forward
validated points model, a schedule-neutral ratings model, and an MIP squad
optimiser that beats greedy selection in 5/5 backtested seasons. It has its
own self-contained warehouse, structurally similar to `ptb-core`'s in places
but not compatible with it.

Two ways to bring it into this monorepo were considered: lift-and-shift
(move it in with its own separate database, keep its own ingestion) or
re-platform (point its derived models at `ptb-core`'s warehouse instead of
its own). This spec commits to **re-platform**, on the reasoning that a
second permanent warehouse defeats the point of `ptb-core` existing — one
archive, one set of source tables, shared across every project — and that
reasoning was affirmed after the tradeoff (a large rewrite of validated code,
blocked on infrastructure that does not exist yet) was raised directly.

## Decisions

| Decision | Choice |
|---|---|
| New package | `packages/ptb-fpl`, depends on `ptb-core`, follows the same layout convention |
| Database | The shared `ptb.duckdb` — not a second warehouse file |
| Migration order | Fix `ptb-core` gaps first (identity wiring, snapshot history), then build the gold layer, then port the modeling code — never port modeling code against a moving schema |
| Validation gate | Nothing is declared "ported" until it reproduces fpl-app's own documented numbers (5/5 optimiser-vs-greedy, the RPS 0.6443 calibration figure, the minutes-model log-loss numbers) against the new schema |
| fpl-app's own ingestion | Left untouched and read-only during the port — it is the reference implementation the validation gate checks against, not something to delete early |

## What already lines up (confirmed by reading both schemas)

This is not a from-scratch translation. `ptb-core`'s schema was built with
fpl-app's tables as a reference, and it shows:

- `dim_player.fpl_code` exists specifically as the bridge to fpl-app's
  `dim_player.player_code` — the FPL-stable identity spine fpl-app is built
  on already has a designated landing spot.
- `src_fotmob_player_stat` / `src_fotmob_team_stat` are the same long/tidy
  `(league, season, stat_name, entity_id) -> value` shape as fpl-app's
  `fact_player_fotmob_stat` / `fact_team_fotmob_stat`, including the same
  reasoning in both codebases' comments (a wide table would be mostly NULL
  because the stat menu is season-dependent).
- `src_footballdata_odds` mirrors `fact_match_odds` column-for-column
  (confirmed by PR #6's own description, which says so directly).
- `src_draftkings_odds` covers the same ground as `fact_fixture_forecast`.

## Confirmed gaps

1. **Player identity is unfinished** (tracked in `CLAUDE.md`'s known-gaps
   section already). `map_player_source` has no FotMob rows yet, and nothing
   in `ptb rebuild` calls the resolvers that exist. Every fpl-app model that
   joins Understat or FotMob data onto a player (minutes, points, the
   Championship-prior work) depends on this bridge existing and being
   populated. **This blocks everything below it and must land first.**

2. **No price/ownership snapshot history.** `src_fpl_element` is keyed
   `(season, element_id)` with `INSERT OR REPLACE` — every ingest overwrites
   the previous state, so only the latest snapshot survives. fpl-app's
   `fact_player_snapshot` is keyed `(season, captured_at, player_code)`, a
   true time series, which is what `v_price_change` and any price-move
   modeling (fpl-app roadmap project 10) read. `ptb-core` needs a new
   snapshot-history table before that capability can port — this is a
   genuine schema addition, not a renaming exercise.

3. **No gold layer at all**, declarative or computed:
   - Declarative views: nothing like fpl-app's `v_match_odds`,
     `v_fixture_run`, `v_player_points`, etc. exist. PR #6 named
     `v_match_odds` as the concrete first one; the rest of `views.sql`
     (720 lines) is the template for what else is needed, translated onto
     `ptb-core` table names.
   - Computed facts: fpl-app treats several outputs as warehouse tables that
     a Python module computes and writes back (`fact_team_rating`,
     `fact_player_minutes`, `fact_player_points`, everything under
     `optimise/`). None of this exists on `ptb-core` in any form — this is
     where the bulk of the 13k lines actually lives, and it is Python
     algorithm code, not SQL, so "building the gold layer" for FPL means
     both new views and a new package of ported modeling code.

4. **No entry/personal-squad tracking.** `fact_entry_snapshot`,
   `fact_entry_pick`, `fact_entry_transfer` have no `ptb-core` equivalent.
   Net-new tables either way, plus fpl-app's own note that these three are
   excluded from its replay-from-archive pattern (`PRESERVED_TABLES`) because
   they need correlated multi-endpoint loading rather than the uniform
   one-endpoint-at-a-time loader `rebuild` otherwise uses — same
   accommodation will be needed in `ptb-core`'s `load.py`.

## Architecture: phased build order

Each phase is a real merge-able unit, not a milestone inside one giant
branch — matching how every other feature in this repo has shipped.

1. **Finish player identity** (Tasks 9–12 of the existing plan): FotMob
   resolution, `player_overrides.yaml`, wire the resolver into `ptb
   rebuild`/`coverage`, validate against fpl-app's `map_player_external` as
   a known-good reference. This was already the top of the gap list before
   this spec existed; this port is a second, independent reason it has to
   land first.
2. **Schema gap-fill.** Add the snapshot-history table, the entry-tracking
   tables, and whatever else Phase 3's view-by-view translation surfaces
   that isn't in (1)'s list — treat this list as a starting point, not
   exhaustive; a full column-by-column diff of both schemas hasn't been done
   yet and belongs in the implementation plan, not this spec.
3. **Gold layer, declarative half.** Translate `fpl-app/src/fpl/warehouse/views.sql`
   onto `ptb-core` table names, view by view, starting with `v_match_odds`
   (already scoped by PR #6's description) and `v_player_current`.
4. **Gold layer, computed half.** New `packages/ptb-fpl`, containing
   `minutes.py`, `ratings.py`, `points/`, `optimise/` ported with their
   internal math untouched but every query repointed at `ptb-core`'s schema
   and the views from (3). Highest-risk phase — see below.
5. **CLI and personal tracking.** Port `entry.py`/`team.py` and the relevant
   `__main__.py` commands onto `ptb-fpl`.
6. **Validation gate.** Re-run fpl-app's own `--validate`/`--calibrate`/
   `--check` paths against the re-platformed warehouse and confirm the
   headline numbers match what `docs/roadmap.md` already documents (5/5
   optimiser-vs-greedy, RPS 0.6443, the minutes-model walk-forward figures).
   Nothing from fpl-app is retired until this passes.

## Risk

Phase 4 is where a silent mistake is most dangerous: a schema-mapping error
in a query (wrong join, subtly different column semantics) does not raise an
exception — it just quietly changes a statistically-validated model's
output. This is exactly the failure mode the Phase 6 validation gate exists
to catch, and it should not be treated as optional or deferred. Every model
being ported already has a documented baseline number in
`../fpl-app/docs/roadmap.md` to check against.

## Out of scope

- Any of the *unbuilt* roadmap projects (the simulation harness, transfer
  planning, effective ownership, etc.) — this spec is about relocating what
  already works, not building what doesn't exist yet.
- `soccerdata` / `pulp` dependency additions to `ptb-fpl`'s `pyproject.toml`
  — mechanical, belongs in the implementation plan.
- StatsBomb, WSL — unrelated to this port.

## Open questions for the plan

- A full column-by-column diff between fpl-app's schema and `ptb-core`'s
  hasn't been done — Phase 2's gap list above is a floor, not a ceiling.
- Whether the snapshot-history gap is best closed by adding `captured_at` to
  a new table (`src_fpl_element_snapshot`) or by changing `src_fpl_element`'s
  primary key — the former avoids disturbing every existing consumer of
  "latest state," which is the safer default, but this deserves a real
  decision in the plan rather than an assumption in this spec.
- How long fpl-app's own ingestion stays running in parallel after Phase 6
  passes — probably "until confident," not a hard cutover date.
