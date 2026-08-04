# Player Identity — Design

**Date:** 2026-08-03
**Status:** Approved
**Scope:** Spec #4. `dim_player`, `map_player_source`, and the cross-source player resolver.
**Builds on:** `docs/superpowers/specs/2026-08-02-universal-soccer-data-layer-design.md`

## Purpose

Every source in the warehouse names players differently, and nothing currently
reconciles them. `dim_team` and `dim_match` exist; there is no player identity at
all. Until there is, no query can follow a player from the FPL API to Understat
shots to FotMob stat boards — which is most of what the `sacked-in-the-morning`
port needs.

There is a second, quieter gap. `src_fpl_element` is keyed on
`(season, element_id)`, and FPL reassigns `element_id` between seasons. Without a
player dimension the warehouse cannot follow a player across seasons **even
within FPL alone**, so ten seasons of `src_fpl_player_gw` are effectively ten
disconnected datasets.

This spec closes both.

## Correction to the foundational spec

The universal data layer design states that `sacked-in-the-morning`'s
`map_player_external` "is hand-maintained and cannot be re-derived", calls it "a
permanent manual liability inside an otherwise reproducible system", and treats a
derived `map_player_source` as a deliberate departure from existing practice.

**This is factually wrong**, and the correction matters because it changes what
this spec is for. Every one of the table's 2,132 rows carries an auto-generated
provenance note:

| `notes` | rows |
|---|---|
| `auto: understat name match` | 1,827 |
| `auto: fotmob name match` | 305 |

Zero rows are hand-curated. The map is already 100% derived by name matching.

So there is no manual liability to migrate and nothing to preserve. What the
existing table provides instead is a **measurable baseline** to beat, against a
`dim_player` population of 2,710:

| Bridge | Mapped | Coverage |
|---|---|---|
| Understat | 1,827 | 67% |
| FotMob | 2,076 | 77% |

The foundational spec should be amended to remove the "manual liability" framing.

## Decisions

| Decision | Choice |
|---|---|
| Scope | Competition-agnostic table and resolver shape; only `fpl`/`vaastav`/`understat`/`fotmob` wired and validated. ASA/NWSL resolution defers to a later spec. |
| Matching strategy | Hybrid — deterministic tiers for unambiguous matches, scored fallback for the tail. |
| Placement | `ptb-core`. Player identity is universal data, not app-specific. |
| Success bar | Beat the 67%/77% baseline on coverage, plus a hand-audit of the scored tier for precision. |
| Ambiguity | Fails to `unresolved_player` with a recorded reason. Never guesses. |
| Manual overrides | `player_overrides.yaml`, committed to git, applied last and unconditionally. |

### Why hybrid rather than one mechanism

The easy ~75% of matches is already solved — that is what the 67%/77% baseline
*is*. All the remaining value sits in the tail, where a pure rule cascade needs
ever-more-baroque tiers and a scorer is genuinely better at weighing partial
evidence. Keeping the unambiguous tiers deterministic means the bulk of matches
stay explainable and cheap, and the scorer only has to justify itself on cases
the baseline already fails.

If the scored layer does not beat the baseline in the audit, it is deleted and
what remains is a plain deterministic cascade. No foundation is wasted either way.

## Schema

Three tables in `packages/ptb-core/src/ptb/core/warehouse/schema.sql`, shaped to
match the existing team and match identity tables.

```sql
CREATE SEQUENCE IF NOT EXISTS seq_player_id START 1;

CREATE TABLE IF NOT EXISTS dim_player (
    player_id         BIGINT PRIMARY KEY,
    canonical_name    TEXT NOT NULL,
    normalized_name   TEXT NOT NULL,   -- NOT unique: homonyms are real
    birth_date        DATE,
    nationality       TEXT,
    fpl_code          INTEGER,         -- cross-season FPL spine; NULL off-PL
    opta_code         TEXT,
    first_seen_season TEXT,
    last_seen_season  TEXT
);

CREATE TABLE IF NOT EXISTS map_player_source (
    player_id        BIGINT NOT NULL,
    source           TEXT NOT NULL,
    source_player_id TEXT NOT NULL,
    method           TEXT NOT NULL,
    confidence       DOUBLE NOT NULL,
    PRIMARY KEY (source, source_player_id)
);

CREATE TABLE IF NOT EXISTS unresolved_player (
    source           TEXT NOT NULL,
    source_player_id TEXT NOT NULL,
    reason           TEXT NOT NULL,
    detail           TEXT,            -- top-2 candidates and their scores
    PRIMARY KEY (source, source_player_id)
);
```

### `normalized_name` carries no UNIQUE constraint

`dim_team.normalized_name` is UNIQUE. `dim_player.normalized_name` deliberately
is not: two different Danny Wards have played in the Premier League. Even
`(normalized_name, birth_date)` is not safe to constrain, because `birth_date` is
only 40% populated. Uniqueness is a resolver concern, enforced by the ambiguity
rules below, not by the schema.

### FPL's `source_player_id` is `code`, never `element_id`

`element_id` is reassigned between seasons, so using it would collide under
`PRIMARY KEY (source, source_player_id)` — element 100 in 2020/21 is a different
human from element 100 in 2024/25. `code` is stable across seasons and is what
makes `dim_player` able to span them. This is the same reason
`sacked-in-the-morning` keys on `player_code`.

### vaastav gets no rows in `map_player_source`

vaastav's rows already carry per-season FPL `element_id`, and
`src_fpl_player_gw` already stores both sources on that key. vaastav therefore
reaches `player_id` by joining
`src_fpl_element (season, element_id) → code → map_player_source`, and needs no
name matching at all. Only Understat and FotMob are real matching problems.

### Season-scoped attributes stay out

Team and position change per season, and `src_fpl_element` already records both
per `(season, element_id)`. `dim_player` holds only attributes true of the
person. A conformed view in the views spec can expose the season-scoped join.

### Prerequisite absorbed from the source gap-fill spec

Tiers 1 and 2 cannot function until `src_fpl_element` captures `birth_date` and
`opta_code`. Both are present in the archived FPL bootstrap payload and simply
are not loaded. This spec adds those two columns and the loader change.

Their coverage is limited and the resolver must not assume otherwise —
`opta_code` is populated for 564 players (exactly the current season) and
`birth_date` for 1,094 of 2,710 (40%). They are recent API additions, so they are
**disambiguators for current players, not a join key across ten seasons.**

## Resolver

A new module `packages/ptb-core/src/ptb/core/identity/players.py`, beside
`teams.py` and `matches.py` and following their shape.

### Row creation order

FPL creates `dim_player` rows first — it has the richest attributes and is the
Premier League spine. Understat and then FotMob match *into* existing rows; where
no match is found they create a row of their own, which is how Big-5 players who
never appear in FPL enter the dimension.

### Blocking

Candidates are restricted to the same `(competition, season)`. At roughly 600
players per block the pairwise comparison cost is negligible, and the restriction
removes most homonym risk for free.

### Deterministic tiers

Evaluated in order; first hit wins.

| Tier | Rule | `method` | `confidence` |
|---|---|---|---|
| 1 | `opta_code` equal, both non-null | `opta_code` | 1.00 |
| 2 | `normalized_name` equal + `birth_date` equal | `name_dob` | 0.99 |
| 3 | `normalized_name` equal + same team + same season, unique in block | `name_team_season` | 0.95 |

### Scored fallback

Everything reaching this stage is scored as a weighted sum of name similarity,
team agreement, position agreement, and date-of-birth agreement over the
candidates in the block.

Not every feature is available for every pair. Understat shot rows carry no
position, and `birth_date` is absent for 60% of players. A feature that is
missing on either side is **excluded from the sum and the weights renormalized
over those remaining**, rather than scored as zero — otherwise a missing
`birth_date` would penalise a pair that is in fact a perfect name-and-team match.
Name similarity is always available and is the only feature that can never be
excluded.

Name similarity is Jaro-Winkler taken as the **maximum over name variants** —
full name, first-initial plus surname, and surname alone — because Understat
writes `Gabriel Fernando de Jesus` where FPL's `web_name` is `Jesus`.

A candidate is accepted when both hold:

- `score >= 0.80`
- `best_score - runner_up_score >= 0.10`

Both are module-level constants, tuned against the audit. The margin requirement
is what prevents a confident-looking wrong match when two players in the same
block score similarly.

Anything else is written to `unresolved_player` with reason `no_candidate` or
`ambiguous`, and `detail` carrying the top two candidates and their scores. That
detail doubles as the review queue for the precision audit.

Normalization reuses the existing diacritic stripping in `identity/text.py`.

### Overrides

`packages/ptb-core/src/ptb/core/identity/player_overrides.yaml`, committed to
git, is applied last and unconditionally with `method='override'` and
`confidence=1.0`. It can also assert a *non*-match — the only available way to
correct a confidently-wrong scored match without loosening a threshold for
everyone.

### Rebuild wiring

The resolver truncates and rebuilds `map_player_source` and `unresolved_player`
from the `src_` tables on every run, exactly as the `resolve_*` functions in
`matches.py` do. This keeps the map fully re-derivable, which was the goal the
foundational spec was reaching for.

One ordering constraint: `warehouse/load.py`'s `rebuild()` currently resolves
each source inside its own `if written.get(...)` branch. Player resolution is
inherently cross-source and must run **once after the entire loop completes**,
not per source.

## Validation

| Check | Bar |
|---|---|
| Understat coverage | > 67% |
| FotMob coverage | > 77% |
| Scored-tier precision | ≥ 98% on a random sample of 50 — at most one bad match |

**Coverage must be measured over a comparable population.** The baseline
percentages come from `sacked-in-the-morning`'s `dim_player`, which holds only
players who have appeared in FPL. ptb's `dim_player` will be strictly larger,
because Understat contributes Big-5 players who never appear in FPL at all.
Comparing raw percentages across the two would be meaningless.

Coverage is therefore computed over `dim_player WHERE fpl_code IS NOT NULL` —
the subset that corresponds to the baseline population. Coverage across the
full dimension is worth reporting alongside it, but it is not the bar.

Precision is sampled from the **scored tier only**. The deterministic tiers are
not where wrong answers come from, and including them would dilute the sample
into uselessness. Confidently-wrong matches are the failure mode that matters
here: a missing match is visible, a wrong one silently corrupts every downstream
query.

A one-off diff harness compares the result against `sacked-in-the-morning`'s
`map_player_external`, reporting agreement, ptb-only, sitm-only, and outright
conflicts. Conflicts are the interesting output and should be worked through
before sign-off. This ships as a script, not production code — it exists to
validate one migration.

### Tests

- One case per deterministic tier.
- A homonym case: two same-named players in one block resolve separately or fail.
- A diacritic case: `Ødegaard` / `Odegaard` resolve together.
- An ambiguity case asserting the row lands in `unresolved_player` rather than
  being guessed.
- Idempotence: two consecutive rebuilds produce identical tables.

`ptb verify` is extended to report `unresolved_player` counts by source and
reason.

## Out of scope

- ASA / NWSL player resolution — a later spec. NWSL shares no players with the
  wired sources, so it is a disjoint problem sharing only the table shape.
- Conformed `v_*` views and the source precedence table — the next spec, which
  builds on this one.
- FBref identifiers. `map_player_external.fbref_id` is empty in
  `sacked-in-the-morning` and nothing depends on it.
- Any porting of modelling code.

## Follow-on work this unblocks

The conformed views spec, and after it the `ptb-fpl` package that owns the ported
model-output tables (`fact_player_points`, `fact_player_minutes`,
`fact_team_rating`, the fixture projection tables) and the FPL entry tables.
Those stay out of `ptb-core` so that "the warehouse is rebuildable from the
archive" remains literally true.
