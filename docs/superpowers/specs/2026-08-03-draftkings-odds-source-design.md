# DraftKings Odds Source — Design

**Date:** 2026-08-03
**Status:** Approved
**Scope:** Spec #3b-ii. The DraftKings forward-odds source.
**Builds on:** `docs/superpowers/specs/2026-08-02-universal-soccer-data-layer-design.md`

## Purpose

The FPL app's `fdr` solver rates the gameweek *ahead*, which needs odds for
fixtures that have not been played yet. football-data.co.uk only publishes a
match once it is settled, so forward odds come from DraftKings' public
sportsbook API. This is the last source dataset the FPL app reads that is not
yet in park-the-bus; porting it means every source the modelling consumes lives
in the universal warehouse.

DraftKings is a deliberately swappable choice. It is a single book gated on a
fragile client fingerprint, and it may be replaced later with another odds
source. The source is therefore kept isolated behind the standard `Source`
interface so a future swap touches nothing else.

## Decisions

| Decision | Choice |
|---|---|
| Markets | Moneyline (1X2) and Total Goals Over/Under 2.5 -- what the `fdr` solver needs. |
| League | Premier League only (`40253`). |
| Odds storage | Raw decimal odds, source-faithful. De-vigging into probabilities stays with the `fdr` solver / a future conformed view. |
| Snapshots | Append-only. Odds are live, so every run archives a fresh timestamped snapshot; not incremental. |
| Resolution | `resolve_draftkings` maps events into `dim_match` (`E0`), joining the FPL forward fixture and, once settled, football-data. |
| Failure handling | The fingerprint/geo gate raises `Blocked`; the source catches it, logs, and returns zero payloads so a run never aborts on DraftKings. |
| Access | Raw HTTP with `requests` and the web client's fingerprint headers. |

## Context

The endpoint behind the DraftKings league pages is
`GET https://sportsbook-nash.draftkings.com/sites/{site}/api/sportscontent/controldata/league/leagueSubcategory/v1/markets`
with `site = US-CO-SB`, an OData `eventsQuery`/`marketsQuery` filter per league
and subcategory, and `include=Events`. Two subcategories make a full price:
moneyline `4514` (1X2, `outcomeType` Home / Tie / Away) and total goals `13171`
(Over/Under; the 2.5 line is the one the solver uses). Two requests per refresh.

Akamai gates the endpoint on the web client's fingerprint headers (`x-client-*`,
`x-pe-*`); origin and referer alone return 403. `x-client-version` is pinned to a
web build number and will eventually stop being accepted -- the source treats
that as routine failure, not an error to abort on. Confirmed working live on
2026-08-03 (200, ten upcoming fixtures with 1X2 and O/U selections).

The response carries `events` (`{id, name, startEventDate, participants:[{name,
venueRole}]}`), `markets` (`{id, eventId}`), and `selections` (`{marketId,
outcomeType, points, displayOdds:{decimal}}`). DraftKings labels the draw `Tie`.

## Architecture

All additions live in `ptb-core`, following the Plan 1/2/3 module layout.

### Source (`ptb ingest draftkings`)

Fetch both subcategories for the Premier League and archive them together in one
verbatim envelope (`endpoint = "markets"`, one snapshot per run). A `Blocked`
raised by the fingerprint/geo gate is caught inside `ingest`, logged, and
returns an empty list -- the run continues and other sources are unaffected.

Snapshots are append-only: DraftKings odds move continuously, so each run writes
a fresh timestamped payload rather than skipping already-archived ones.

### Warehouse table (source-faithful `src_draftkings_odds`)

One row per event per snapshot:

- `dk_event_id`, `captured_at`, `season`, `kickoff_utc`, `home_team`, `away_team`,
  `moneyline_home`, `moneyline_draw`, `moneyline_away`, `over_2_5`, `under_2_5`,
  `archive_key`. PK `(dk_event_id, captured_at)`.

Odds are the raw decimal prices exactly as DraftKings gives them. The `fdr`
solver's de-vig (removing the bookmaker margin to get probabilities) is a
derivation that ports with the app, or becomes a conformed `v_` view later. The
season is derived from the kickoff date (an August 2026 kickoff is `2026/27`).

### Resolution

`resolve_draftkings` reads the latest snapshot per event, resolves the two team
names through the alias table, and maps each event into `dim_match` under
competition `E0` via the existing +/-36h window. Because DraftKings and the FPL
fixtures list are both forward-looking, an upcoming match carries DraftKings odds
and its FPL fixture on one `dim_match`; once the match is played, football-data
joins the same row. It hooks into `rebuild()` after the loader, like the other
resolve steps. Unresolved events are recorded, never dropped.

### CLI

- `ptb ingest draftkings`; `ptb ingest all` picks it up via the registry.
- `ptb rebuild` gains the loader and `resolve_draftkings`.
- `ptb coverage` already unions match-bearing `src_*` tables; DraftKings events
  are matches, so a `src_draftkings_odds` arm is added (competition `E0`).

## Testing

- **Golden payloads.** A trimmed real moneyline + totals envelope, committed so
  the loader is testable offline and the payload shape stays documented against a
  source this fragile.
- **De-vig is not tested here** because it is not done here; the loader stores raw
  odds, and a test asserts the raw decimal prices round-trip.
- **Idempotency.** Loading the same snapshot twice leaves the warehouse
  unchanged.
- **Rebuild determinism.** A full replay reproduces an identical warehouse.
- **Resolution.** A DraftKings event and an FPL fixture for the same upcoming
  match resolve to one `dim_match`.
- **Blocked handling.** A source whose fetch raises `Blocked` returns an empty
  list rather than propagating.

## Error handling

- `Blocked` (fingerprint/geo gate, non-JSON, request error) is caught in
  `ingest`; the run logs and continues. This is the defining behaviour of the
  source.
- A source failing mid-ingest leaves the archive valid.
- Loaders are idempotent; re-running after a failure is safe.
- Unparseable payloads are logged and skipped, the raw payload retained.
- Resolution failures produce recorded unresolved rows.

## Out of scope

- Odds markets beyond 1X2 and Over/Under 2.5.
- Leagues other than the Premier League.
- The de-vigged-probability conformed view (belongs with the FPL modelling port
  or the conformed-layer spec).
- Replacing DraftKings with another odds provider (the source is kept swappable
  so this is a later, isolated change).
- The football-data odds enrichment (a separate, optional change).

## Open questions deferred to plan-writing

- The exact selection/participant field names are pinned against the live API
  (already probed: `selections[].displayOdds.decimal`, `events[].participants[]`
  with `venueRole` Home/Away, `startEventDate`).
