# Understat and ASA Sources — Design

**Date:** 2026-08-03
**Status:** Approved
**Scope:** Spec #2. Two new sources on top of the Plan 1 data layer.
**Builds on:** `docs/superpowers/specs/2026-08-02-universal-soccer-data-layer-design.md`

## Purpose

Plan 1 stood up the archive → warehouse → identity spine and proved it with a
single, unfragile source (football-data.co.uk). This spec adds the next two
sources:

- **Understat** — shot-level xG for the Big 5 men's leagues. This is the first
  *second opinion* on Big-5 data, which is what makes source precedence (a Plan
  3 concern) meaningful rather than theoretical.
- **ASA** (American Soccer Analysis, `itscalledsoccer`) — goals-added (g+), xG,
  and xPass for NWSL, MLS, and USL. This brings the first women's competition
  (NWSL) into the warehouse and exercises the ±36-hour match-resolution window
  that Plan 1 built specifically for late NWSL kickoffs.

Everything here follows the shape Plan 1 established: each source is a `Source`
that fetches verbatim payloads into the archive, plus a loader that replays them
into source-faithful `src_*` tables, plus a `resolve_*` step that maps its
matches into `dim_match`. Nothing is merged or reconciled — that is Plan 3.

## Decisions

| Decision | Choice |
|---|---|
| Sources in this spec | Understat and ASA only. FotMob and StatsBomb open data defer to a later spec. |
| Understat depth | Match-level **and** shot-level xG. Shot-level is Understat's signature and the truest second opinion. |
| Understat coverage | Big 5 men (EPL, La Liga, Bundesliga, Serie A, Ligue 1), all seasons back to 2014/15. |
| ASA coverage | NWSL, MLS, and USL — a deliberate widening of the Plan 1 coverage decision (see below). |
| ASA depth | Games, game-level xG, and player-level goals-added / xG / xPass. |
| Access method | Raw HTTP with `requests` for both. No client library. Preserves the verbatim-archive contract. |
| Ingest re-runs | Incremental by default (skip already-archived keys); a `--refetch` flag forces a full re-pull. |
| Player identity | Deferred to Plan 3. Player rows are stored verbatim in `src_*` tables, unresolved. |
| Conformed layer | Deferred to Plan 3. No `v_*` views or precedence table here. |

### Deliberate deviation from the Plan 1 coverage decision

Plan 1 set target coverage at "Big 5 + NWSL + WSL" and explicitly excluded
MLS/USL. This spec includes MLS and USL through ASA anyway. The justification:
ASA's API is identical across its leagues — the only difference is a league code
in the path — so MLS/USL cost almost nothing to collect, and the g+ data is
likely useful to the deferred WAR project. This is a conscious expansion, not an
oversight. NWSL remains the in-scope competition that matters for this spec's
stated purpose; MLS/USL come along because they are nearly free.

## Context

- **Understat** was reworked to a JavaScript shell in late 2025; season data now
  comes from a plain JSON endpoint (`GET getLeagueData/{league}/{season}`)
  rather than embedded HTML. `sacked-in-the-morning` already ports the league
  endpoint. The post-2025 per-match shot endpoint is pinned during plan-writing
  by probing the live site.
- **ASA** publishes a free, documented REST API at
  `app.americansocceranalysis.com/api/v1/{league}/...`. The official
  `itscalledsoccer` Python client wraps it but returns parsed DataFrames; we call
  the HTTP API directly so the archived payload stays verbatim.
- `soccer-gar` (the FotMob shot-map and StatsBomb code) is **not** available on
  this machine, which is the practical reason those two sources wait for a later
  spec.

## Architecture

All additions live in the existing `ptb-core` package and follow Plan 1's
module layout. No new package.

### New warehouse tables

All are `src_*` — source-faithful, typed, never merged. Keyed by each source's
own native ids.

**Understat**

- `src_understat_match` — understat match id (PK), league, season, kickoff, home
  team, away team, home goals, away goals, home xG, away xG, forecast (w/d/l).
- `src_understat_shot` — understat shot id (PK), match id, minute, player name,
  player understat id, team, home/away, xG, result, situation, shot type,
  location (x, y), assist player.

**ASA** — one faithful table per endpoint; a `league` column distinguishes
NWSL / MLS / USL.

- `src_asa_game` — game id (PK), league, season, date, home team, away team,
  home score, away score.
- `src_asa_game_xgoals` — game id + team, xG for/against and shot counts.
- `src_asa_player_goals_added` — player id + season + league, g+ by action type.
- `src_asa_player_xgoals` — player id + season + league, xG / xA / key passes.
- `src_asa_player_xpass` — player id + season + league, pass volume / completion /
  xPass.

**Dimension seed additions** — `dim_competition` gains `NWSL` (gender `W`),
`MLS` (`M`), and `USL` (`M`).

Because payloads are archived whole, a table whose columns prove incomplete is a
re-parse from the archive, never a re-fetch.

### Source fetch shapes

**Understat** (`ptb ingest understat [--refetch]`)

1. Per league-season: `GET getLeagueData/{league}/{season}` → `{teams, players,
   dates}`. Archived under endpoint `league`, one key per league-season. `dates`
   is the match list (ids plus match-level xG).
2. Per match id: fetch the match shot payload. Archived under endpoint `match`,
   one key per match. Incremental — skipped when its key already exists unless
   `--refetch` is set.

Understat leagues map to Plan 1 competition codes: EPL→E0, La_liga→SP1,
Bundesliga→D1, Serie_A→I1, Ligue_1→F1. Understat seasons are named by start year
(2024 == 2024/25) and are converted to the `2024/25` warehouse form at load time.

**ASA** (`ptb ingest asa [--refetch]`)

Per league, fetch `games`, `games/xgoals`, `players/goals-added`,
`players/xgoals`, and `players/xpass`, season-filtered, from
`app.americansocceranalysis.com/api/v1/{league}/...`. One archived payload per
(league, endpoint, season). NWSL seasons are single calendar years (`"2025"`),
matching the convention Plan 1 already used for NWSL.

Each source declares its own politeness policy (rate limit, retry, User-Agent)
in one place.

### Match resolution and identity

Both `resolve_*` functions reuse Plan 1's `resolve_match` with its ±36-hour
window, and both hook into `rebuild()` after their loaders run, exactly like
`resolve_footballdata`.

- **`resolve_understat(con)`** maps each `src_understat_match` to a competition
  code and resolves it with Understat's native match id. Because these are Big-5
  men's matches, they resolve **against football-data's existing `dim_match`
  rows** — the first genuine cross-source match identity in the project. This
  only works if team names conform, so team aliases are checked and extended
  (below).
- **`resolve_asa(con)`** resolves each `src_asa_game` under NWSL / MLS / USL.
  NWSL's late kickoffs crossing the UTC date boundary are exactly the case the
  ±36-hour window was designed for.

Unresolved rows land in `unresolved_match` with a recorded reason, never
dropped.

### Team aliases

Understat's Big-5 club spellings are spot-checked against Plan 1's canonical
names, with aliases added where they differ, so Understat matches conform with
football-data's. NWSL, MLS, and USL clubs are added for ASA. The alias file
stays hand-maintained and committed, as in Plan 1.

### CLI

- `ptb ingest understat [--refetch]` and `ptb ingest asa [--refetch]`. `ptb
  ingest all` picks both up automatically via the source registry.
- `ptb rebuild` gains the two loaders and the two resolve steps.
- `ptb coverage` is generalized from a football-data-only query to a **union
  across all `src_*` match tables**, producing a true source × competition ×
  season grid.

## Testing

Following Plan 1's approach:

- **Golden payloads.** A trimmed real response committed per source per endpoint
  — Understat `league` and `match`, and each ASA endpoint — so loaders are
  testable offline and payload shape stays documented if a source goes dark.
- **Idempotency.** Loading the same payload twice leaves the warehouse
  unchanged.
- **Rebuild determinism.** A full replay reproduces an identical warehouse.
- **Incremental ingest.** A second ingest run with an already-populated archive
  fetches nothing new; `--refetch` re-pulls.
- **Identity regression — the headline test.** A known Understat Big-5 match must
  resolve to the *same* `dim_match` as its football-data counterpart (the
  cross-source join proof). Plus a deliberate late-kickoff NWSL fixture via ASA
  resolves correctly through the ±36-hour window.

## Error handling

Inherited from Plan 1 and unchanged:

- A source failing mid-ingest leaves the archive valid — payloads are written per
  response, so a partial run is a smaller archive, never a corrupt one. Combined
  with incremental ingest, resuming a failed Understat shot sweep is free.
- Loaders are idempotent, so re-running after a failure is always safe.
- Unparseable payloads are logged and skipped, not fatal; the raw payload stays
  in the archive for a later fixed parser.
- Identity-resolution failures produce recorded unresolved rows, surfaced by
  `ptb verify` (built in Plan 3).

## Out of scope

- FotMob and StatsBomb open data (a later spec).
- The conformed layer: `v_*` views and the precedence table (Plan 3).
- Player identity: `map_player_source` (Plan 3).
- Any modelling, API, or dashboard.

## Open questions deferred

- The exact post-2025 Understat per-match shot endpoint is confirmed against the
  live site during plan-writing.
- ASA API pagination and season-filter parameters are confirmed against the live
  API during plan-writing.
