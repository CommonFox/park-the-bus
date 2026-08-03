# FPL Data Sources — Design

**Date:** 2026-08-03
**Status:** Approved
**Scope:** Spec #3a. The FPL data sources ported from `sacked-in-the-morning`.
**Builds on:** `docs/superpowers/specs/2026-08-02-universal-soccer-data-layer-design.md`

## Purpose

`sacked-in-the-morning` is the Fantasy Premier League project that park-the-bus
is meant to absorb. Its data collection rests on several external sources;
football-data.co.uk (Spec 1) and Understat (Spec 2) are already ported. This
spec ports the two that carry the actual fantasy data:

- **FPL API** (`fantasy.premierleague.com`, public endpoints) — the official
  source of players, teams, gameweeks, fixtures, and per-gameweek scoring for
  the current season.
- **vaastav backfill** (`vaastav/Fantasy-Premier-League` on GitHub) — cleaned
  per-gameweek CSVs back to 2016/17. The official API only serves the current
  season, so this community repo is the only way to populate history.

Porting these unblocks moving the FPL project onto the shared layer. A second
spec (3b) will follow with DraftKings forward odds and FotMob season stat-tables.

## Decisions

| Decision | Choice |
|---|---|
| Placement | All sources register in `ptb-core` alongside footballdata/understat/asa. No separate `ptb-fpl` package in this spec. |
| Sources here | FPL API and vaastav backfill only. DraftKings and FotMob defer to Spec 3b. |
| FPL endpoints | `bootstrap-static`, `fixtures`, and the per-player `element-summary` sweep. |
| Excluded endpoints | `entry`, `entry-picks`, `league-standings`, `event-live` — manager/live-specific, not objective game data. |
| Fixture identity | FPL fixtures resolve into `dim_match` (competition `E0`), joining football-data and Understat on one match. |
| Player identity | Deferred to the conformed-layer spec. FPL element ids and names stored verbatim. |
| Ingest re-runs | Incremental by default; `--refetch` forces a re-pull. |
| Seasons | Stored `2024/25`. Current season for the API; `2016/17`+ for vaastav. |

## Context

`sacked-in-the-morning` models FPL in a full star schema
(`dim_player`, `dim_team`, `dim_event`, `dim_fixture`, `fact_player_gw`, and
more). This port does **not** copy that schema wholesale. park-the-bus keeps
source-faithful `src_*` tables and layers conformed `dim_*` / `v_*` on top, so
the FPL data lands in `src_fpl_*` and resolves into the existing `dim_team` /
`dim_match`. The reconciled/derived layer stays a later concern.

The FPL API's public endpoints need no auth. `bootstrap-static` is a single
large payload (all players, teams, events, positions); `element-summary/{id}`
is one request per player (~600 for a Premier League season).

## Architecture

All additions live in `ptb-core`, following the Plan 1/2 module layout.

### FPL API source (`ptb ingest fpl [--refetch]`)

Three archived payload shapes, each a verbatim envelope carrying source/season/
fetch metadata:

1. `bootstrap-static` → endpoint `bootstrap`, one payload.
2. `fixtures` → endpoint `fixtures`, one payload.
3. `element-summary/{id}` → endpoint `element`, one payload per player id drawn
   from bootstrap. Throttled. Incremental: within a gameweek a re-run skips
   already-archived players unless `--refetch`; a run after a new deadline
   captures the newly finished gameweek.

The current FPL season is derived from bootstrap's events (the season spanning
the gameweek deadlines) and stored in `2024/25` form.

### vaastav backfill source (`ptb ingest vaastav [--refetch] [--season ...]`)

Per season, fetch `players_raw.csv` and `gws/merged_gw.csv` from the GitHub raw
host, each wrapped in its own envelope. Incremental: a season already archived
is skipped unless `--refetch`, since historical CSVs are static. Seasons default
to all published (2016/17 onward).

### Warehouse tables (source-faithful `src_fpl_*`)

- `src_fpl_element` — bootstrap player snapshot: element id (PK), names, team id,
  element_type, now_cost, total_points, form, selected_by_percent, status, and
  the core scoring aggregates.
- `src_fpl_team` — team id (PK), name, short_name, strength ratings
  (overall/attack/defence × home/away).
- `src_fpl_event` — gameweek id (PK), name, deadline_time, finished, is_current,
  is_next, average/highest score.
- `src_fpl_position` — element_type id (PK), singular_name, short name
  (GKP/DEF/MID/FWD).
- `src_fpl_fixture` — fixture id (PK), event, kickoff_time, team_h, team_a,
  team_h_score, team_a_score, finished, difficulty (h/a), season.
- `src_fpl_player_gw` — the key fact, one row per player × gameweek: element id,
  event, fixture, opponent, minutes, total_points, goals, assists, clean_sheets,
  bonus, bps, expected stats where present, value, selected, transfers, plus a
  `source` column distinguishing `api` from `vaastav`.
- `src_fpl_element_season` — vaastav `players_raw` per-season snapshot: season,
  element id, name, position, team, total_points, minutes.

Because payloads are archived whole, a table whose columns prove incomplete is a
re-parse from the archive, never a re-fetch.

### Identity and resolution

- **`src_fpl_team` → `dim_team`.** FPL club spellings ("Spurs", "Man Utd",
  "Nott'm Forest") resolve through the alias table, most already covered by
  Plan 1, a few added.
- **`src_fpl_fixture` → `dim_match`** (`resolve_fpl`). FPL fixtures are Premier
  League matches with a UTC kickoff and home/away FPL team ids (names via
  `src_fpl_team`). They resolve into `dim_match` under competition `E0` through
  the existing ±36h window, so one PL match carries football-data, Understat,
  and FPL together. This is the headline cross-source result of the spec.
- **FPL player identity is deferred.** Element ids and names sit in `src_fpl_*`
  for the later conformed spec, where FPL will be a strong player-identity
  anchor (name + team + position).

`resolve_fpl` hooks into `rebuild()` after the FPL loaders, exactly like
`resolve_footballdata` / `resolve_understat` / `resolve_asa`.

### CLI and coverage

- `ptb ingest fpl [--refetch]` and `ptb ingest vaastav [--refetch] [--season]`;
  `ptb ingest all` picks both up via the registry.
- `ptb rebuild` gains the FPL and vaastav loaders plus `resolve_fpl`.
- `ptb coverage` (already a union across match-bearing `src_*` tables) adds
  `src_fpl_fixture`, so FPL appears in the source × competition × season grid.

## Testing

- **Golden payloads.** Trimmed real responses committed per endpoint: FPL
  `bootstrap`, `fixtures`, one `element-summary`, and vaastav `players_raw` +
  `merged_gw` CSV samples. Loaders are testable offline and the payload shape
  stays documented.
- **Idempotency.** Loading the same payload twice leaves the warehouse
  unchanged.
- **Rebuild determinism.** A full replay reproduces an identical warehouse.
- **Incremental ingest.** vaastav skips already-archived seasons; `--refetch`
  re-pulls. The FPL element sweep skips already-archived players within a
  gameweek.
- **Headline identity regression.** One Premier League fixture resolves to the
  same `dim_match` across football-data, Understat, and FPL.

## Error handling

Inherited from Plans 1–2 and unchanged:

- A source failing mid-ingest leaves the archive valid — payloads are written per
  response, so a partial run is a smaller archive, never a corrupt one, and
  incremental ingest makes resumption free.
- Loaders are idempotent, so re-running after a failure is always safe.
- Unparseable payloads are logged and skipped, not fatal; the raw payload stays
  in the archive for a later fixed parser.
- Identity-resolution failures produce recorded unresolved rows.

## Out of scope

- DraftKings forward odds and FotMob season stat-tables (Spec 3b).
- The football-data odds enrichment (Spec 1 kept a minimal odds set; expanding it
  is a separate, optional change).
- FPL modelling: points projection, minutes model, squad optimiser, backtesting.
  These sit above the warehouse and belong to the eventual FPL project spec.
- The conformed layer (`v_*` views, precedence table) and player identity
  (`map_player_source`).
- The FPL API's manager/live endpoints (`entry`, `entry-picks`,
  `league-standings`, `event-live`).

## Open questions deferred to plan-writing

- Exact `bootstrap-static`, `fixtures`, and `element-summary` field sets are
  pinned against the live API and `sacked-in-the-morning`'s `schema.sql`.
- The vaastav repo's per-season CSV paths and column headers (which vary by era)
  are confirmed against the live GitHub repo.
