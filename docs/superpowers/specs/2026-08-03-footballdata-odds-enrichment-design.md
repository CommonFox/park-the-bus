# football-data Odds Enrichment — Design

**Date:** 2026-08-03
**Status:** Approved
**Scope:** Spec #3b-iii. A richer odds set from the football-data source.
**Builds on:** `docs/superpowers/specs/2026-08-02-universal-soccer-data-layer-design.md`

## Purpose

Plan 1 kept only three Bet365 1X2 columns from the football-data CSV, which
carries a much larger odds block. `sacked-in-the-morning`'s odds path curates a
richer set -- Bet365, Pinnacle, market average and max, over/under 2.5, and the
Asian handicap -- used for a market fixture-difficulty rating and implied
clean-sheet / goals expectations. This spec brings that curated set into
park-the-bus.

It is the last odds work: DraftKings (Spec 3b-ii) covers forward odds on
unplayed fixtures; football-data covers settled-match odds and is the historical
market record. Together they give the FPL app's odds inputs.

## Decisions

| Decision | Choice |
|---|---|
| Placement | A new `src_footballdata_odds` table, separate from `src_footballdata_match`. |
| Existing odds columns | `odds_home/odds_draw/odds_away` move out of `src_footballdata_match` into `src_footballdata_odds` as `b365_h/d/a`. |
| Source / fetch | Unchanged. No new fetch -- the CSV is already archived, so this is a loader + schema change replayed by `ptb rebuild`. |
| Competitions | Every competition football-data ingests (the Big 5), not just the Premier League -- the odds columns exist in every file. |
| Curated set | Bet365 / Pinnacle / Max / Avg 1X2 (opening and closing), over/under 2.5 (B365 + Avg, opening and closing), and Asian handicap (opening and closing). |
| Asian handicap | Kept though measured negligible for FDR -- the CSV is free and a documented negative result needs the data to re-test. |

## Context

football-data files carry 100+ columns; the odds block grew over the eras.
Pinnacle, market average/max, closing odds, and the Asian handicap appear only in
later seasons (closing odds and Asian handicap from ~2019/20). A 1993/94 file has
no odds at all. The loader already reads by column name and tolerates absence, so
missing columns simply load as NULL and any consumer needs a fallback.

The curated columns mirror `sacked-in-the-morning`'s `fact_match_odds`, which was
built and measured against real outcomes; reusing its column selection keeps the
FPL app's odds reads a direct port.

## Architecture

A schema addition and a loader extension in `ptb-core`. No source change.

### New table `src_footballdata_odds`

One row per match, sharing `src_footballdata_match`'s natural key so the two
join directly:

- Key: `competition`, `season`, `match_date`, `home_team`, `away_team`.
- 1X2 opening: `b365_h/d/a`, `ps_h/d/a`, `max_h/d/a`, `avg_h/d/a`.
- 1X2 closing: `b365c_h/d/a`, `psc_h/d/a`, `avgc_h/d/a`.
- Over/under 2.5: `over25_b365/under25_b365`, `over25_avg/under25_avg`,
  `over25_avgc/under25_avgc`.
- Asian handicap opening: `ah_line`, `ah_home_avg/ah_away_avg`,
  `ah_home_ps/ah_away_ps`.
- Asian handicap closing: `ahc_line`, `ahc_home_avg/ahc_away_avg`,
  `ahc_home_ps/ahc_away_ps`.
- `archive_key`.
- PK `(competition, season, match_date, home_team, away_team)`.

`src_footballdata_match` loses its `odds_home/odds_draw/odds_away` columns; those
values now live in `src_footballdata_odds` as `b365_h/d/a`.

### Loader

`load_footballdata` already parses each CSV row into a `src_footballdata_match`
row. It is extended to also emit a `src_footballdata_odds` row from the same
parsed row, reading the curated odds columns by their football-data names (pinned
against a real file during plan-writing). Both inserts use `INSERT OR REPLACE`, so
idempotency and rebuild determinism are unchanged.

Rows with no odds at all (e.g. 1993/94) still get a `src_footballdata_odds` row
keyed to the match, with every odds column NULL -- or, to avoid empty rows, are
skipped; the plan picks one and tests it. (Decision: **write a row only when at
least one 1X2 price is present**, so odds-less eras add nothing.)

### No new resolution or coverage

`src_footballdata_odds` is not a match-bearing table in its own right -- it hangs
off `src_footballdata_match`, which already resolves into `dim_match`. So there
is no new resolve step and no coverage change.

## Testing

- **Golden payload.** The existing football-data golden fixture is extended (or a
  second fixture added) with the richer odds columns from a real file, and a test
  asserts they map into `src_footballdata_odds`.
- **Odds moved.** A test asserts `b365_h/d/a` are in `src_footballdata_odds` and no
  longer in `src_footballdata_match`.
- **Era tolerance.** The 1993/94 no-odds case writes a `src_footballdata_match`
  row and **no** `src_footballdata_odds` row.
- **Idempotency and rebuild determinism.** Unchanged; re-covered for the new
  table.

## Out of scope

- Any new fetch or source change (the CSV is already archived).
- De-vigging odds into probabilities (a conformed `v_` view, later).
- Odds from books beyond those football-data publishes.
- The DraftKings forward-odds source (already done, Spec 3b-ii).

## Open questions deferred to plan-writing

- The exact football-data CSV column names for each curated field (`PSH`, `MaxH`,
  `AvgH`, `B365CH`, `PSCH`, `AvgCH`, `B365>2.5`, `Avg>2.5`, `AHh`, `B365AHH`,
  `PAHH`, `AvgAHH`, and closing equivalents) are pinned against a real modern
  E0 file.
