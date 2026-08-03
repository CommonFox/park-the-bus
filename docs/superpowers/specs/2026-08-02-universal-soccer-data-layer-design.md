# Universal Soccer Data Layer — Design

**Date:** 2026-08-02
**Status:** Approved
**Scope:** Spec #1 of several. Sourcing and warehouse infrastructure only.

## Purpose

`park-the-bus` is a personal monorepo for soccer analytics. Four projects will
eventually live in it:

- **GAR** — a single number grading how entertaining a match was (ported from `soccer-gar`)
- **FPL** — Fantasy Premier League research (ported from `sacked-in-the-morning`)
- **WAR** — a wins-above-replacement equivalent for soccer (not yet scoped)
- **NWSL** — analysis of the National Women's Soccer League (not yet scoped)

All four need the same underlying data. This spec designs the shared layer that
collects it — and nothing else. Each project gets its own spec later, built on
top of this one.

## Decisions

| Decision | Choice |
|---|---|
| Migration approach | Greenfield. Existing repos are *ported* onto the new layer, not moved as-is. |
| Competition coverage | Big 5 (England, Spain, Italy, Germany, France) + NWSL + WSL |
| Storage model | Append-only raw archive is the source of truth; DuckDB warehouse is derived and disposable |
| Archive location | Local-only for now; backend kept swappable for a future move to object storage |
| Multi-source conflicts | Source-faithful storage, conformed views with explicit precedence |
| v1 sources | football-data.co.uk, FotMob, Understat, ASA, StatsBomb open data |
| Tooling | `uv` workspace, one package per project |

## Context: the data landscape as of August 2026

**FBref's advanced statistics were removed in January 2026.** Sports Reference
pulled them after StatsPerform terminated FBref's access to Opta feeds over an
alleged agreement violation, plausibly connected to Opta's exclusive FIFA
betting-data arrangement for World Cup 2026. FBref had been the one free source
matching this project's exact coverage shape — Big 5 plus WSL plus NWSL, with xG
and possession-adjusted statistics. There is no equivalent replacement, and the
loss fell hardest on women's football.

Two consequences drive this design:

1. **No universal source exists.** The layer must be multi-source and
   source-attributed from the start. This is not a future-proofing flourish; it
   is the only way to cover the target competitions at all.
2. **Archived data may become irreplaceable.** FBref demonstrated that a source
   relied on by everyone can vanish without notice, taking its history with it.
   An archive of scraped payloads is therefore not a convenience — it may be the
   only surviving copy.

### Available sources

| Source | Coverage | Granularity | Health | Existing code |
|---|---|---|---|---|
| football-data.co.uk | 11 countries all divisions, +16 more; back to 1993/94 | Match results, shots, corners, cards, bookmaker odds | Static CSV, very stable | `sacked-in-the-morning` (PL only) |
| FotMob | Broad, including NWSL and WSL | Shot maps, match stats | Current, scraped, fragile | `soccer-gar` |
| Understat | Big 5 men only, back to 2014 | Shot-level xG | Stable | `sacked-in-the-morning` |
| ASA (`itscalledsoccer`) | NWSL, MLS, USL | Player/team/match, g+, xG, xPass | Free documented API | None — new build |
| StatsBomb open data | Scattered: World Cups, WSL 2018–20, NWSL 2018, Messi La Liga | Full event stream | Stable, not current | `soccer-gar` |
| Sofascore | Broad | Shot maps | Scraped, fragile | `soccer-gar` — deferred, redundant with FotMob |

Note that football-data.co.uk and Understat cover **no** women's competitions.
NWSL and WSL rest entirely on FotMob, ASA, and StatsBomb open data.

## Architecture

### Repo layout

```
park-the-bus/
├── pyproject.toml              # uv workspace root
├── uv.lock
├── packages/
│   └── ptb-core/
│       ├── pyproject.toml
│       └── src/ptb/core/
│           ├── archive/        # keyed raw archive, swappable backend
│           ├── sources/        # one module per provider + registry
│           ├── identity/       # match, team and player resolution
│           ├── warehouse/      # schema.sql, views.sql, idempotent loaders
│           ├── config.py
│           └── cli.py          # the `ptb` command
├── data/                       # gitignored
│   ├── raw/                    # the archive — source of truth
│   └── ptb.duckdb              # derived, disposable
├── docs/superpowers/specs/
└── tests/
```

Later specs add `packages/ptb-gar`, `ptb-fpl`, `ptb-war` and `ptb-nwsl`. Each
depends on `ptb-core`; none depends on another.

### The central rule

**Fetching and loading never touch each other.**

- `ptb ingest <source>` fetches and writes verbatim payloads to the archive. It
  does not write to DuckDB.
- `ptb rebuild` replays the archive into DuckDB. It does not touch the network.

A parser bug becomes a re-parse rather than a re-fetch. A schema redesign costs
nothing in re-scraping. This property is inherited from `sacked-in-the-morning`,
where it has already proven itself, and it matters more here because the sources
are more fragile.

### Archive

`RawArchive` is addressed by key, not by path:

```
{source}/{endpoint}/{label}__{stamp}.json.gz
```

`write(key, payload)` and `read(key)` delegate to a backend. `LocalBackend` is
the only implementation in this spec. An object-storage backend for Cloudflare
R2 or Backblaze B2 can be added later as a constructor change rather than a
refactor.

This is a deliberate correction: the existing `RawArchive` in
`sacked-in-the-morning` exposes `Path` in its public signature, which would make
that later swap invasive.

Payloads are written verbatim and never mutated. Each source declares its own
politeness policy — rate limit, retry behaviour, User-Agent — in one place
rather than scattered through fetch code.

Estimated archive size at target coverage (~2,060 matches per season, ten
seasons, gzipped): 2–5 GB. Comfortably local; comfortably inside object-storage
free tiers when the time comes.

### Warehouse schema

Four layers in DuckDB. The archive itself is the true raw layer — files, not
tables.

| Layer | Examples | Rule |
|---|---|---|
| `src_*` | `src_fotmob_shot`, `src_footballdata_match`, `src_asa_player_game` | Source-faithful. Typed and normalized, never merged or reconciled. |
| `dim_*` | `dim_match`, `dim_team`, `dim_player`, `dim_competition`, `dim_season` | Conformed entities, one row per real-world thing. |
| `map_*` | `map_match_source`, `map_team_source`, `map_player_source` | Bridges a `dim_` surrogate id to each source's native id. |
| `v_*` | `v_match`, `v_shot`, `v_player_match` | Conformed views applying source precedence. |

Source precedence lives in a **table** — `(measure, source, priority)` — not in
code. So `xg` resolving StatsBomb → Understat → FotMob is data that can be
queried and changed without touching the loaders.

Nothing is averaged or silently reconciled. Where sources disagree, both values
remain in their `src_` tables and the view picks one by declared priority.

### Identity resolution

**Matches.** `soccer-gar` computes a `match_key` of `YYYY-MM-DD|teamA-teamB`
using the UTC date and a normalized, order-independent team pair. This works for
World Cup fixtures but breaks silently on NWSL: a 7:30pm Pacific Saturday
kickoff is 02:30 UTC on Sunday, so a source reporting local date and a source
reporting UTC generate two different keys for one match, and every cross-source
join returns nothing without erroring.

`dim_match` therefore gets a surrogate id, resolved on
`(competition, season, normalized team pair)` within a **±36 hour kickoff
window** rather than exact date equality. `match_key` is retained as a
human-readable debugging column, not as a join key.

If more than one candidate falls inside the window — two legs of a fixture
played within 36 hours, which should not happen but must not corrupt data if it
does — resolution fails rather than guessing. The rows stay unmapped with a
recorded reason and `ptb verify` reports them.

Seasons are stored as `2024/25` throughout, matching the convention already used
in `sacked-in-the-morning`. Sources expressing seasons as a single start year
(football-data's `2425`, Understat's `2024`) are converted at load time.

**Teams.** An alias table committed to git, seeded from `soccer-gar`'s existing
`TEAM_ALIASES`, plus diacritic stripping. Small, stable, and hand-maintained
without embarrassment — roughly 200 clubs.

**Players.** This is the hard case, and the one place the design deliberately
departs from existing practice. `sacked-in-the-morning`'s `map_player_external`
is hand-maintained and cannot be re-derived, so a rebuild has to specially
preserve it — a permanent manual liability inside an otherwise reproducible
system.

Instead, `map_player_source` is **derived**: normalized name plus team plus
season, plus date of birth where a source provides it, storing `confidence` and
`method` on every row. A small hand-curated override file at
`packages/ptb-core/src/ptb/core/identity/player_overrides.yaml`, committed to
git, always wins over derived rows. The map becomes re-derivable, the manual portion
shrinks to genuine ambiguities, and what remains manual is version-controlled
rather than living only inside a database file.

## CLI

```bash
ptb ingest footballdata --competition E0,SP1,D1,I1,F1 --season 2025/26
ptb ingest all --season 2025/26
ptb rebuild                    # archive → warehouse, no network
ptb coverage                   # competition × season × source grid
ptb status                     # archived vs loaded, gaps
ptb verify                     # integrity and identity checks
```

`ptb coverage` is not a nicety. With five sources across two dozen competitions
and ten seasons, "what do I actually have" stops being answerable from memory
almost immediately.

## Testing

- **Golden payloads.** A small committed sample of real responses per source.
  Loaders are testable offline, payload shape is documented, and if a source goes
  dark the reference survives. Given FBref, this is not hypothetical.
- **Idempotency.** Loading the same snapshot twice leaves the warehouse
  unchanged.
- **Rebuild determinism.** A full replay of the archive produces an identical
  warehouse. The entire design rests on this property, so it is tested
  explicitly.
- **Identity regressions.** Known cross-source match pairs must resolve to a
  single `dim_match`, including a deliberate late-kickoff NWSL fixture — the case
  that would otherwise fail silently.

## Error handling

- A source failing mid-ingest leaves the archive valid: payloads are written per
  response, so a partial run is simply a smaller archive, never a corrupt one.
- Loaders are idempotent, so re-running after a failure is always safe.
- Unparseable payloads are logged and skipped rather than aborting a rebuild; the
  raw payload remains in the archive for a later fixed parser.
- Identity resolution failures produce unmapped rows with a recorded reason, not
  dropped data. `ptb verify` surfaces them.

## Build order

1. Workspace skeleton, config, CLI shell
2. Archive with local backend
3. Source protocol and registry
4. **football-data.co.uk** — static CSVs, no scraping fragility. Proves archive →
   warehouse → views end to end while delivering Big 5 back to 1993/94.
   Generalizes the existing PL-only loader, whose `COMPETITION = "E0"` constant
   is the only thing restricting it, and retains the match statistics (shots,
   corners, fouls, cards) that the FPL version discards.
5. Warehouse schema, `dim`/`map` tables, rebuild
6. Identity: match resolution and team aliases
7. FotMob — women's coverage and shot detail
8. Understat — a second opinion on Big 5, which is what makes precedence real
9. ASA — NWSL depth
10. StatsBomb open data — event-level ground truth
11. Conformed views and the precedence table
12. `coverage`, `status`, `verify`

## Out of scope

Explicitly not in this spec:

- WAR metric design (deferred until the warehouse exists and its real coverage is
  known)
- The GAR port, the FPL port, NWSL analysis
- Any modelling, API, or dashboard
- Sofascore ingestion (redundant with FotMob for v1)
- The object-storage archive backend — the seam is built, the implementation is
  not

## Open questions deferred to later specs

- Whether WAR is feasible on free data, and at what scope. Full event streams
  across Big 5 do not exist freely post-FBref. ASA already publishes g+ for NWSL,
  which is both a benchmark and a reason not to rebuild that particular wheel.
  This is answered after `ptb coverage` reports what the warehouse actually holds.
- How much of `sacked-in-the-morning`'s modelling code (points, minutes, squad
  optimiser, backtesting) ports unchanged. Most of it sits above the warehouse, so
  the answer depends on how closely the conformed views match its current schema.
