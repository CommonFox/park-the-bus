# FotMob Stats Source — Design

**Date:** 2026-08-03
**Status:** Approved
**Scope:** Spec #3b-i. The FotMob season stat-tables source.
**Builds on:** `docs/superpowers/specs/2026-08-02-universal-soccer-data-layer-design.md`

## Purpose

The FPL app being ported from `sacked-in-the-morning` reads FotMob season
stat-tables (`fact_player_fotmob_stat`, `fact_team_fotmob_stat`) for player and
team form inputs. FotMob is the last source dataset the app depends on that is
not yet in park-the-bus. Porting it means every *source* the FPL app consumes
lives in the universal warehouse, and porting the modelling itself becomes a
pure app-layer job with no data blockers.

This is the FotMob *stat-table* endpoint (`leagueseasondeepstats`), which is
what `sacked-in-the-morning` uses -- season stat leaderboards, not the shot-map
scraper that lived in the absent `soccer-gar` repo.

## Decisions

| Decision | Choice |
|---|---|
| Endpoint | `leagueseasondeepstats` -- season stat leaderboards, player and team. |
| Leagues | Premier League (47) and Championship (48), matching `sacked-in-the-morning`. The league set is a dict, so further leagues are a one-line addition later. |
| Seasons | All seasons FotMob offers for the league (~11, back to ~2014/15). |
| Stats | The full per-season `statsList` (every stat FotMob exposes), both `players` and `teams`. |
| Resolution | None. FotMob stat-tables have no fixtures/matches, so there is nothing to resolve into `dim_match`. |
| Identity | Deferred. FotMob player/team ids and names are stored verbatim; conforming them to `dim_team` / player identity is the conformed-layer work the FPL port will build on. |
| Ingest re-runs | Incremental by `(league, season, stat, type)`; `--refetch` re-pulls (for the live season). |
| Access | Raw HTTP with `requests`. A plain session works; the browser's signed `x-mas` header is not enforced. |

## Context

`sacked-in-the-morning`'s `fotmob.py` drives the endpoint
`GET https://www.fotmob.com/api/data/leagueseasondeepstats?id={league}&season={seasonId}&stat={stat}&type={players|teams}`.
One request returns one stat's leaderboard for one season, so a full sweep is
`len(statsList)` requests per season per type. `statsList` is season-aware
(recent seasons expose ~37 stats, older ones fewer), so the sweep is driven off
each season's own `statsList` rather than a fixed list. Asking for a stat a
season does not offer returns 200 with empty `statsData`, never an error.

FotMob names seasons `2024/2025`; the warehouse stores `2024/25`. The `season`
query parameter is a numeric season **id** (e.g. 23685), discovered from the
`seasons` array every response carries.

## Architecture

All additions live in `ptb-core`, following the Plan 1/2/3 module layout.

### Source (`ptb ingest fotmob [--refetch]`)

Three-phase, per league:

1. Fetch a probe stat (`goals`, `players`) to read the `seasons` array -- the
   id-to-name map for every season the league has.
2. For each requested season, fetch a probe per type to read that season's
   `statsList`.
3. For each `(stat, type)` in the season's `statsList`, fetch the leaderboard and
   archive it verbatim. Incremental: a `(league, season, stat, type)` already in
   the archive is skipped unless `--refetch`, so completed seasons are pulled
   once and only the live season is refreshed.

Each archived payload is a verbatim envelope wrapping the full API response
(which carries `statsData`, `statsList`, `seasons`, `leagueDetails`), plus the
league, season label, stat, and type.

### Warehouse tables (source-faithful `src_fotmob_*`)

- `src_fotmob_player_stat` -- league_id, league_name, season, season_id,
  stat_name, stat_title, stat_category, fotmob_player_id, fotmob_team_id,
  player_name, position_code, value, substat_value, rank.
  PK `(league_id, season, stat_name, fotmob_player_id)`.
- `src_fotmob_team_stat` -- league_id, league_name, season, season_id, stat_name,
  stat_title, stat_category, fotmob_team_id, team_name, value, substat_value,
  rank. PK `(league_id, season, stat_name, fotmob_team_id)`.

Each `statsData` row carries the stat value nested under `statValue` and a paired
secondary value under `substatValue`; the loader reads the stat's `title` and
`category` from the payload's `statsList` metadata by matching the stat name.
Because payloads are archived whole, a table whose columns prove incomplete is a
re-parse from the archive, never a re-fetch.

### No resolution, and coverage

FotMob stat-tables carry no fixtures, so there is no `resolve_fotmob` and nothing
enters `dim_match`. `ptb coverage` is a match-count grid, so FotMob does not
appear in it; this is expected, not a gap. (A future `ptb status` in the
conformed-layer spec is the natural place to report non-match sources.)

### CLI

- `ptb ingest fotmob [--refetch]`; `ptb ingest all` picks it up via the registry.
- `ptb rebuild` gains the FotMob loader. No resolve step.

## Testing

- **Golden payloads.** A trimmed real response for one player stat and one team
  stat (each carrying `statsData`, `statsList`, `seasons`), committed so the
  loader is testable offline and the payload shape stays documented.
- **Idempotency.** Loading the same payload twice leaves the warehouse
  unchanged.
- **Rebuild determinism.** A full replay reproduces an identical warehouse.
- **Incremental ingest.** A second run skips already-archived
  `(league, season, stat, type)`; `--refetch` re-pulls.
- **Stat metadata join.** A loaded row carries the correct `stat_title` and
  `stat_category` looked up from `statsList`.

## Error handling

Inherited from Plans 1-3 and unchanged:

- A source failing mid-ingest leaves the archive valid; incremental ingest makes
  resumption free.
- Loaders are idempotent, so re-running after a failure is always safe.
- Unparseable payloads are logged and skipped, not fatal; the raw payload stays
  in the archive for a later fixed parser.
- A stat a season does not offer returns empty `statsData`, which the loader
  writes as zero rows rather than treating as an error.

## Out of scope

- Leagues beyond the Premier League and Championship (a later, trivial extension).
- The FotMob shot-map endpoint (a different source, not needed by the FPL app).
- Conforming FotMob ids to `dim_team` / player identity (the conformed layer).
- DraftKings forward odds and the football-data odds enrichment (a following
  spec, once FotMob lands).
- The FPL modelling port itself (points, minutes, ratings, optimiser).

## Open questions deferred to plan-writing

- The exact `statsList` metadata fields (`title`, `category`, substat naming) are
  pinned against the live API.
- The full set of team-type stats (`statsList` for `type=teams`) is confirmed
  against the live API.
