# Player Identity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Status as of 2026-08-09:** Tasks 1–8 shipped via
> [PR #7](https://github.com/CommonFox/park-the-bus/pull/7). Tasks 9, 10, 11
> executed and committed this session (`514d5a7`, `439ca46`, `2ae320c`),
> exactly as originally drafted — no edits needed. This is also **Phase 1**
> of [`specs/2026-08-08-fpl-replatform-design.md`](../specs/2026-08-08-fpl-replatform-design.md).
>
> **Task 12 (validation) is in progress, not complete.** Running the matcher
> against a real 10-season, five-league archive for the first time — rather
> than the synthetic same-season fixtures every unit test uses — surfaced
> three real bugs in the pre-existing `names.py`/`players.py` matching logic
> (not introduced by Tasks 9–11): fuzzy false-collisions between short
> surnames, foreign-league transfers never getting a second chance to match
> once their earliest appearance had no candidates, and a truncated-name
> coincidence being treated as certain. All three fixed and unit-tested
> (`931225d`, `7b04f80`, `772fb95`), plus two bugs in this task's own
> `compare_player_map.py` script (`8e241f4`). **Not done:** a final rebuild +
> validation run with all three fixes applied together — each cycle takes
> ~80 minutes against the real archive, and the last one only covered fixes
> 1 and 2. Matched players climbed 0 → 1,191 → 1,625 → 2,865 across the
> session's cycles; the coverage bars below (67%/77%) have not been
> re-checked since fix 3. Run `uv run ptb rebuild` then
> `uv run python scripts/compare_player_map.py data/ptb.duckdb
> ../fpl-app/data/fpl.duckdb` to pick this up.

**Goal:** Give the warehouse a `dim_player` dimension and a re-derivable cross-source player map, so a query can follow one footballer from the FPL API to Understat to FotMob, and across seasons.

**Architecture:** A new `identity/players.py` beside the existing `teams.py`/`matches.py`. FPL's stable `code` builds the spine of `dim_player`; Understat and FotMob players are matched into it by three deterministic tiers, then a scored fallback with a threshold and a runner-up margin. Ambiguity records itself in `unresolved_player` instead of guessing. A committed `player_overrides.yaml` is applied last and always wins. The whole map is rebuilt from `src_` tables on every `ptb rebuild`.

**Tech Stack:** Python 3.12, DuckDB, PyYAML, rapidfuzz (new), pytest.

## Global Constraints

- Python `>=3.12`; the workspace is uv-managed with packages under `packages/*`.
- All new library code lives in `packages/ptb-core/src/ptb/core/`.
- Tests live in the repo-root `tests/` directory, flat, named `test_<topic>.py`.
- Seasons are the string `2024/25` everywhere. Every source already normalizes to this at ingest — no conversion belongs in this plan.
- Loaders and resolvers never touch the network.
- Every resolver is idempotent: running it twice produces identical tables.
- Ambiguity is recorded, never guessed. Follow the precedent in `matches.py`.
- Run tests with `uv run pytest` from the repo root.

## Deviations from the spec

Three, all discovered while mapping the spec onto the actual codebase. Each is
implemented as described here rather than as written in the spec.

1. **`ptb verify` does not exist.** The spec says verify is extended to report
   `unresolved_player`. There is no such subcommand — `ptb coverage` is what
   reports `unresolved_match` today. Task 11 extends `coverage` instead.

2. **The `no_candidate` unresolved reason is dropped.** The spec asks both for
   unmatched rows to become new `dim_player` rows *and* for them to be recorded
   as `no_candidate` in `unresolved_player`. Those contradict. Creating the row
   is the correct behaviour — it is how Big-5 players who never appear in FPL
   enter the dimension — so `AMBIGUOUS` is the only reason in the vocabulary.

3. **Position agreement is not a scoring feature.** The spec lists it. No source
   in scope exposes a position vocabulary comparable to FPL's `element_type`:
   Understat shot rows carry no position at all, and FotMob's `position_code` has
   no published mapping. Inventing one would fabricate signal. The scorer uses
   name, team, and birth date; the weights are renormalized over what is present.

## File Structure

| File | Responsibility |
|---|---|
| `packages/ptb-core/src/ptb/core/identity/text.py` | *(modify)* add non-decomposing diacritic folding |
| `packages/ptb-core/src/ptb/core/identity/names.py` | *(create)* pure name-comparison and scoring primitives — no DB |
| `packages/ptb-core/src/ptb/core/identity/players.py` | *(create)* the resolver: spine, tiers, scored fallback, per-source drivers, overrides |
| `packages/ptb-core/src/ptb/core/identity/player_overrides.yaml` | *(create)* committed manual corrections |
| `packages/ptb-core/src/ptb/core/identity/__init__.py` | *(modify)* export the new modules |
| `packages/ptb-core/src/ptb/core/warehouse/schema.sql` | *(modify)* player identity tables; two new `src_fpl_element` columns |
| `packages/ptb-core/src/ptb/core/warehouse/loaders/fpl.py` | *(modify)* load `birth_date` and `opta_code` |
| `packages/ptb-core/src/ptb/core/warehouse/load.py` | *(modify)* call the resolver after the whole loop |
| `packages/ptb-core/src/ptb/core/cli.py` | *(modify)* report `unresolved_player` in `coverage` |
| `packages/ptb-core/pyproject.toml` | *(modify)* add `rapidfuzz` |
| `scripts/compare_player_map.py` | *(create)* one-off validation against sacked-in-the-morning |
| `tests/test_identity_names.py` | *(create)* |
| `tests/test_identity_players.py` | *(create)* |
| `tests/test_fpl_loader.py` | *(modify)* cover the two new columns |
| `tests/fixtures/fpl_bootstrap_sample.json` | *(modify)* add the two new fields |

`names.py` is split from `players.py` deliberately: it is pure, has no DuckDB
dependency, and carries the densest test coverage in the feature. `players.py`
lands at roughly the size of the existing `matches.py` (264 lines), which is the
established shape for a resolver in this codebase.

---

### Task 1: Fold non-decomposing diacritics

`strip_diacritics` uses NFKD, which decomposes `é` into `e` + a combining mark
but leaves `ø`, `ł`, `đ`, and `ß` untouched — they are distinct letters, not
accented ones. So `Ødegaard` and `Odegaard` normalize differently today, and the
spec requires them to resolve together. This also improves team matching
(`Brøndby`), which is why it belongs in the shared module.

**Files:**
- Modify: `packages/ptb-core/src/ptb/core/identity/text.py`
- Test: `tests/test_identity_teams.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `normalize_name(value: str) -> str` — unchanged signature, wider coverage.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_identity_teams.py`:

```python
def test_normalize_folds_non_decomposing_letters():
    """NFKD leaves these letters alone -- they are distinct letters, not
    accented ones -- so they need an explicit translation."""
    from ptb.core.identity.text import normalize_name

    assert normalize_name("Ødegaard") == normalize_name("Odegaard")
    assert normalize_name("Łukasz") == normalize_name("Lukasz")
    assert normalize_name("Đorđević") == normalize_name("Dordevic")
    assert normalize_name("Weiß") == "weiss"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_identity_teams.py::test_normalize_folds_non_decomposing_letters -v`
Expected: FAIL — `assert 'odegaard' == 'odegaard'` fails because the left side is still `ødegaard`.

- [ ] **Step 3: Write the implementation**

In `packages/ptb-core/src/ptb/core/identity/text.py`, add the table after the
existing `_WHITESPACE` definition:

```python
# NFKD decomposes accented letters into a base plus a combining mark, but these
# are distinct letters rather than accented ones and survive it untouched.
_SPECIAL = str.maketrans({
    "ø": "o", "đ": "d", "ð": "d", "ł": "l", "ß": "ss",
    "æ": "ae", "œ": "oe", "þ": "th", "ı": "i",
})
```

and replace the body of `normalize_name` with:

```python
def normalize_name(value: str) -> str:
    """Lowercase, de-accent, drop punctuation, collapse whitespace."""
    folded = strip_diacritics(value).lower().translate(_SPECIAL)
    folded = _PUNCTUATION.sub("", folded)
    return _WHITESPACE.sub(" ", folded).strip()
```

The translation runs after `.lower()`, so only lowercase keys are needed.

- [ ] **Step 4: Run the full test suite**

Run: `uv run pytest -q`
Expected: PASS. `normalize_name` feeds `dim_team.normalized_name`, so a
regression here would surface in the team and match identity tests.

- [ ] **Step 5: Commit**

```bash
git add packages/ptb-core/src/ptb/core/identity/text.py tests/test_identity_teams.py
git commit -m "Fold non-decomposing letters in normalize_name"
```

---

### Task 2: Load `birth_date` and `opta_code` into `src_fpl_element`

Both fields are present in every archived FPL bootstrap payload and neither is
loaded. Matching tiers 1 and 2 cannot function without them.

Coverage is limited and the resolver must not assume otherwise: `opta_code` is
populated only for the current season, `birth_date` for roughly 40% of players.
They are disambiguators, not join keys.

**Files:**
- Modify: `packages/ptb-core/src/ptb/core/warehouse/schema.sql`
- Modify: `packages/ptb-core/src/ptb/core/warehouse/loaders/fpl.py`
- Modify: `tests/fixtures/fpl_bootstrap_sample.json`
- Test: `tests/test_fpl_loader.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `src_fpl_element.birth_date DATE` and `src_fpl_element.opta_code TEXT`.

- [ ] **Step 1: Add the two fields to the test fixture**

In `tests/fixtures/fpl_bootstrap_sample.json`, add to the single element object
(the one with `"id": 11`), alongside `"code": 223094`:

```json
"birth_date": "2001-09-05",
"opta_code": "p223094",
```

- [ ] **Step 2: Write the failing test**

Append to `tests/test_fpl_loader.py`:

```python
def test_bootstrap_loads_birth_date_and_opta_code(con):
    """Both are identity signal for cross-source player matching, and both are
    in every archived bootstrap payload."""
    _load(con, "fpl_bootstrap_sample.json")
    row = con.execute(
        "SELECT birth_date, opta_code FROM src_fpl_element "
        "WHERE season = '2024/25' AND element_id = 11"
    ).fetchone()
    assert row == (dt.date(2001, 9, 5), "p223094")


def test_bootstrap_tolerates_missing_birth_date(con):
    """FPL only began publishing these recently, so historical payloads lack
    them and must still load."""
    payload = json.loads(
        (FIX / "fpl_bootstrap_sample.json").read_text(encoding="utf-8"))
    for element in payload["data"]["elements"]:
        element.pop("birth_date", None)
        element.pop("opta_code", None)
    loader.load_fpl(con, payload, "fpl/no-birth-date")
    row = con.execute(
        "SELECT birth_date, opta_code FROM src_fpl_element "
        "WHERE season = '2024/25' AND element_id = 11"
    ).fetchone()
    assert row == (None, None)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_fpl_loader.py -k "birth_date" -v`
Expected: FAIL with `Binder Error: Referenced column "birth_date" not found`.

- [ ] **Step 4: Add the schema columns**

In `packages/ptb-core/src/ptb/core/warehouse/schema.sql`, inside
`CREATE TABLE IF NOT EXISTS src_fpl_element`, add two columns immediately before
`archive_key`:

```sql
    birth_date                 DATE,
    opta_code                  TEXT,
```

`CREATE TABLE IF NOT EXISTS` will not alter a warehouse that already exists. That
is fine and needs no migration — the warehouse is derived and disposable, and
`ptb rebuild` against a deleted file reproduces it.

- [ ] **Step 5: Add the loader support**

In `packages/ptb-core/src/ptb/core/warehouse/loaders/fpl.py`, add a date coercion
helper beside the existing `_ts`:

```python
def _date(value: Any) -> Optional[dt.date]:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        return None
```

Then extend the `elements` list comprehension — add the two values immediately
before `archive_key`:

```python
    elements = [[season, e["id"], _i(e.get("code")), e.get("web_name"), e.get("first_name"),
                 e.get("second_name"), _i(e.get("team")), _i(e.get("element_type")),
                 _i(e.get("now_cost")), _i(e.get("total_points")), _f(e.get("form")),
                 _f(e.get("selected_by_percent")), e.get("status"), _i(e.get("minutes")),
                 _i(e.get("goals_scored")), _i(e.get("assists")), _i(e.get("clean_sheets")),
                 _i(e.get("bonus")), _i(e.get("bps")), _f(e.get("expected_goals")),
                 _f(e.get("expected_assists")), _f(e.get("expected_goal_involvements")),
                 _date(e.get("birth_date")), e.get("opta_code"),
                 archive_key] for e in data.get("elements", []) if e.get("id") is not None]
```

and update the INSERT to name both columns and carry two more placeholders:

```python
        con.executemany(
            "INSERT OR REPLACE INTO src_fpl_element (season, element_id, code, web_name, "
            "first_name, second_name, team, element_type, now_cost, total_points, form, "
            "selected_by_percent, status, minutes, goals_scored, assists, clean_sheets, "
            "bonus, bps, expected_goals, expected_assists, expected_goal_involvements, "
            "birth_date, opta_code, "
            "archive_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "?, ?, ?, ?, ?, ?, ?)", elements)
```

Count the placeholders: 25. Twenty-three before, plus two.

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_fpl_loader.py -v`
Expected: PASS, all tests in the file.

- [ ] **Step 7: Commit**

```bash
git add packages/ptb-core/src/ptb/core/warehouse/schema.sql \
        packages/ptb-core/src/ptb/core/warehouse/loaders/fpl.py \
        tests/test_fpl_loader.py tests/fixtures/fpl_bootstrap_sample.json
git commit -m "Load FPL birth_date and opta_code"
```

---

### Task 3: Player identity schema

**Files:**
- Modify: `packages/ptb-core/src/ptb/core/warehouse/schema.sql`
- Test: `tests/test_warehouse_db.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `dim_player`, `map_player_source`, `unresolved_player`, `seq_player_id`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_warehouse_db.py`:

```python
def test_player_identity_tables_exist(tmp_path):
    from ptb.core.warehouse import db

    con = db.connect(tmp_path / "test.duckdb")
    try:
        names = {r[0] for r in con.execute(
            "SELECT table_name FROM information_schema.tables").fetchall()}
        assert {"dim_player", "map_player_source", "unresolved_player"} <= names
        assert con.execute("SELECT nextval('seq_player_id')").fetchone()[0] == 1
    finally:
        con.close()


def test_dim_player_allows_homonyms(tmp_path):
    """Two different Danny Wards have played in the Premier League, so
    normalized_name carries no UNIQUE constraint -- unlike dim_team."""
    from ptb.core.warehouse import db

    con = db.connect(tmp_path / "test.duckdb")
    try:
        con.execute(
            "INSERT INTO dim_player (player_id, canonical_name, normalized_name) "
            "VALUES (1, 'Danny Ward', 'danny ward'), (2, 'Danny Ward', 'danny ward')")
        assert con.execute("SELECT count(*) FROM dim_player").fetchone()[0] == 2
    finally:
        con.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_warehouse_db.py -k player -v`
Expected: FAIL — the table set assertion fails, `dim_player` is absent.

- [ ] **Step 3: Add the schema**

Append to `packages/ptb-core/src/ptb/core/warehouse/schema.sql`:

```sql
-- ------------------------------------------------------------ player identity
-- FPL's `code` is stable across seasons where `element_id` is reassigned, so
-- the spine is built on code and map_player_source stores it as the FPL
-- source_player_id. vaastav needs no rows here at all: its data already keys on
-- per-season element_id, so it reaches player_id through
-- src_fpl_element (season, element_id) -> code -> map_player_source.

CREATE SEQUENCE IF NOT EXISTS seq_player_id START 1;

CREATE TABLE IF NOT EXISTS dim_player (
    player_id         BIGINT PRIMARY KEY,
    canonical_name    TEXT NOT NULL,
    -- Deliberately not UNIQUE, unlike dim_team.normalized_name: two different
    -- Danny Wards have played in the Premier League. Uniqueness is the
    -- resolver's problem, enforced by the ambiguity rules, not the schema's.
    normalized_name   TEXT NOT NULL,
    birth_date        DATE,
    nationality       TEXT,
    fpl_code          INTEGER,
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

-- Ambiguous players land here rather than being guessed at. `detail` carries
-- the top two candidates and their scores, which doubles as the review queue.
CREATE TABLE IF NOT EXISTS unresolved_player (
    source           TEXT NOT NULL,
    source_player_id TEXT NOT NULL,
    reason           TEXT NOT NULL,
    detail           TEXT,
    PRIMARY KEY (source, source_player_id)
);
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_warehouse_db.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/ptb-core/src/ptb/core/warehouse/schema.sql tests/test_warehouse_db.py
git commit -m "Add dim_player, map_player_source and unresolved_player"
```

---

### Task 4: Name variants and similarity

Understat writes `Gabriel Fernando de Jesus` where FPL's `web_name` is `Jesus`.
Comparing one canonical string to another loses matches a human finds obvious, so
each name expands into a small variant set and similarity is the best score over
the cross product.

**Files:**
- Create: `packages/ptb-core/src/ptb/core/identity/names.py`
- Modify: `packages/ptb-core/pyproject.toml`
- Test: `tests/test_identity_names.py`

**Interfaces:**
- Consumes: `normalize_name` from `identity/text.py`.
- Produces: `name_variants(full_name: str) -> Set[str]`, `similarity(left: str, right: str) -> float` returning 0.0–1.0.

- [ ] **Step 1: Add the rapidfuzz dependency**

In `packages/ptb-core/pyproject.toml`, extend `dependencies`:

```toml
dependencies = [
    "duckdb>=1.1",
    "requests>=2.32",
    "pyyaml>=6.0",
    "rapidfuzz>=3.9",
]
```

Then run `uv sync` to update the lockfile. Jaro-Winkler is subtle enough that
hand-rolling it would be a liability in the one place the feature's accuracy is
decided.

- [ ] **Step 2: Write the failing test**

Create `tests/test_identity_names.py`:

```python
import pytest

from ptb.core.identity.names import name_variants, similarity


def test_variants_include_the_surname_alone():
    """Understat writes the full legal name; FPL's web_name is often just the
    surname, so the surname must be a comparable variant."""
    assert "jesus" in name_variants("Gabriel Fernando de Jesus")


def test_variants_include_the_initial_form():
    assert "b saka" in name_variants("Bukayo Saka")


def test_variants_of_a_single_word_name_is_just_itself():
    assert name_variants("Rodri") == {"rodri"}


def test_variants_of_an_empty_name_is_empty():
    assert name_variants("   ") == set()


def test_full_name_matches_surname_exactly():
    assert similarity("Gabriel Fernando de Jesus", "Jesus") == pytest.approx(1.0)


def test_initial_form_matches():
    assert similarity("Bukayo Saka", "B. Saka") == pytest.approx(1.0)


def test_diacritics_do_not_block_a_match():
    assert similarity("Martin Ødegaard", "Martin Odegaard") == pytest.approx(1.0)


def test_different_players_score_low():
    assert similarity("Bukayo Saka", "Erling Haaland") < 0.7


def test_similarity_is_symmetric():
    a = similarity("Gabriel Fernando de Jesus", "Jesus")
    b = similarity("Jesus", "Gabriel Fernando de Jesus")
    assert a == pytest.approx(b)


def test_empty_name_scores_zero():
    assert similarity("", "Bukayo Saka") == 0.0
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_identity_names.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ptb.core.identity.names'`.

- [ ] **Step 4: Write the implementation**

Create `packages/ptb-core/src/ptb/core/identity/names.py`:

```python
"""Player name comparison primitives. Pure functions, no database.

Sources write the same footballer very differently. Understat has
'Gabriel Fernando de Jesus'; FPL's web_name is 'Jesus'. Comparing one canonical
string against another therefore loses matches that are obvious to a human, so
each name expands into a small set of variants and similarity is the best score
over the cross product.
"""
from __future__ import annotations

from typing import Set

from rapidfuzz.distance import JaroWinkler

from .text import normalize_name


def name_variants(full_name: str) -> Set[str]:
    """Normalized forms a source might plausibly use for this player."""
    normalized = normalize_name(full_name)
    if not normalized:
        return set()

    variants = {normalized}
    parts = normalized.split()
    if len(parts) > 1:
        surname = parts[-1]
        variants.add(surname)
        variants.add("{} {}".format(parts[0], surname))
        variants.add("{} {}".format(parts[0][0], surname))
    return variants


def similarity(left: str, right: str) -> float:
    """Best Jaro-Winkler score over the two names' variants, 0.0 to 1.0."""
    left_variants = name_variants(left)
    right_variants = name_variants(right)
    if not left_variants or not right_variants:
        return 0.0
    return max(
        JaroWinkler.similarity(a, b)
        for a in left_variants
        for b in right_variants
    )
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_identity_names.py -v`
Expected: PASS, all ten.

- [ ] **Step 6: Commit**

```bash
git add packages/ptb-core/src/ptb/core/identity/names.py \
        packages/ptb-core/pyproject.toml uv.lock tests/test_identity_names.py
git commit -m "Add player name variants and similarity"
```

---

### Task 5: Candidate scoring

**Files:**
- Modify: `packages/ptb-core/src/ptb/core/identity/names.py`
- Test: `tests/test_identity_names.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `score_candidate(*, name_similarity: float, team_agrees: Optional[bool], birth_date_agrees: Optional[bool]) -> float` returning 0.0–1.0, and the `FEATURE_WEIGHTS` dict.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_identity_names.py`:

```python
from ptb.core.identity.names import score_candidate


def test_score_with_only_a_name_is_the_name_similarity():
    assert score_candidate(
        name_similarity=0.9, team_agrees=None, birth_date_agrees=None,
    ) == pytest.approx(0.9)


def test_a_perfect_match_scores_one():
    assert score_candidate(
        name_similarity=1.0, team_agrees=True, birth_date_agrees=True,
    ) == pytest.approx(1.0)


def test_a_missing_birth_date_does_not_penalise():
    """birth_date is absent for 60% of players. Scoring an absent feature as
    zero would sink most true matches below the threshold."""
    with_dob = score_candidate(
        name_similarity=1.0, team_agrees=True, birth_date_agrees=True)
    without_dob = score_candidate(
        name_similarity=1.0, team_agrees=True, birth_date_agrees=None)
    assert without_dob == pytest.approx(with_dob)


def test_a_disagreeing_birth_date_does_penalise():
    """Absent is not the same as contradicted."""
    assert score_candidate(
        name_similarity=1.0, team_agrees=True, birth_date_agrees=False,
    ) < 0.9


def test_a_wrong_team_pulls_a_perfect_name_down():
    assert score_candidate(
        name_similarity=1.0, team_agrees=False, birth_date_agrees=None,
    ) == pytest.approx(0.60 / 0.85)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_identity_names.py -k score -v`
Expected: FAIL with `ImportError: cannot import name 'score_candidate'`.

- [ ] **Step 3: Write the implementation**

Append to `packages/ptb-core/src/ptb/core/identity/names.py` — and add
`Optional` to the `typing` import at the top:

```python
# Name carries most of the signal and is the only feature always available.
# Position is deliberately absent: no source in scope publishes a vocabulary
# comparable to FPL's element_type, and inventing a mapping would fabricate
# signal the data does not contain.
FEATURE_WEIGHTS = {
    "name": 0.60,
    "team": 0.25,
    "birth_date": 0.15,
}


def score_candidate(
    *,
    name_similarity: float,
    team_agrees: Optional[bool],
    birth_date_agrees: Optional[bool],
) -> float:
    """Weighted score over the features available for this pair.

    A feature missing on either side is excluded and the remaining weights are
    renormalized, rather than scored as zero. birth_date is absent for 60% of
    players, so scoring absence as disagreement would sink most true matches.
    Absent and contradicted are different things and score differently.
    """
    features = {"name": float(name_similarity)}
    if team_agrees is not None:
        features["team"] = 1.0 if team_agrees else 0.0
    if birth_date_agrees is not None:
        features["birth_date"] = 1.0 if birth_date_agrees else 0.0

    total_weight = sum(FEATURE_WEIGHTS[name] for name in features)
    weighted = sum(FEATURE_WEIGHTS[name] * value for name, value in features.items())
    return weighted / total_weight
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_identity_names.py -v`
Expected: PASS, all fifteen.

- [ ] **Step 5: Commit**

```bash
git add packages/ptb-core/src/ptb/core/identity/names.py tests/test_identity_names.py
git commit -m "Add candidate scoring with feature renormalization"
```

---

### Task 6: The FPL spine

One `dim_player` row per distinct FPL `code`, which is what lets the dimension
span seasons.

**Files:**
- Create: `packages/ptb-core/src/ptb/core/identity/players.py`
- Modify: `packages/ptb-core/src/ptb/core/identity/__init__.py`
- Test: `tests/test_identity_players.py`

**Interfaces:**
- Consumes: `normalize_name` from `identity/text.py`.
- Produces: `build_fpl_spine(con) -> int` returning rows created.

- [ ] **Step 1: Write the failing test**

Create `tests/test_identity_players.py`:

```python
import datetime as dt

import pytest

from ptb.core.identity import players
from ptb.core.warehouse import db


@pytest.fixture
def con(tmp_path):
    connection = db.connect(tmp_path / "test.duckdb")
    yield connection
    connection.close()


def _fpl_team(con, season, team_id, name):
    con.execute(
        "INSERT OR REPLACE INTO src_fpl_team (season, team_id, name, archive_key) "
        "VALUES (?, ?, ?, 'k')", [season, team_id, name])


def _fpl_element(con, season, element_id, code, first, second, team,
                 birth_date=None, opta_code=None):
    con.execute(
        "INSERT OR REPLACE INTO src_fpl_element (season, element_id, code, web_name, "
        "first_name, second_name, team, element_type, birth_date, opta_code, archive_key) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 3, ?, ?, 'k')",
        [season, element_id, code, second, first, second, team, birth_date, opta_code])


def test_spine_creates_one_player_per_fpl_code(con):
    _fpl_team(con, "2024/25", 1, "Arsenal")
    _fpl_element(con, "2024/25", 11, 223094, "Bukayo", "Saka", 1)
    assert players.build_fpl_spine(con) == 1
    assert con.execute("SELECT count(*) FROM dim_player").fetchone()[0] == 1


def test_one_player_across_two_seasons_is_one_row(con):
    """element_id is reassigned between seasons; code is not. Keying on
    element_id would make one footballer into two players."""
    _fpl_team(con, "2023/24", 1, "Arsenal")
    _fpl_team(con, "2024/25", 1, "Arsenal")
    _fpl_element(con, "2023/24", 7, 223094, "Bukayo", "Saka", 1)
    _fpl_element(con, "2024/25", 11, 223094, "Bukayo", "Saka", 1)

    assert players.build_fpl_spine(con) == 1
    row = con.execute(
        "SELECT canonical_name, fpl_code, first_seen_season, last_seen_season "
        "FROM dim_player").fetchone()
    assert row == ("Bukayo Saka", 223094, "2023/24", "2024/25")


def test_spine_maps_the_fpl_code_not_the_element_id(con):
    _fpl_team(con, "2024/25", 1, "Arsenal")
    _fpl_element(con, "2024/25", 11, 223094, "Bukayo", "Saka", 1)
    players.build_fpl_spine(con)
    row = con.execute(
        "SELECT source, source_player_id, method, confidence FROM map_player_source"
    ).fetchone()
    assert row == ("fpl", "223094", "fpl_code", 1.0)


def test_spine_carries_birth_date_and_opta_code(con):
    """Both arrive only in recent seasons, so the latest non-null wins rather
    than the latest season's value."""
    _fpl_team(con, "2023/24", 1, "Arsenal")
    _fpl_team(con, "2024/25", 1, "Arsenal")
    _fpl_element(con, "2023/24", 7, 223094, "Bukayo", "Saka", 1)
    _fpl_element(con, "2024/25", 11, 223094, "Bukayo", "Saka", 1,
                 birth_date=dt.date(2001, 9, 5), opta_code="p223094")
    players.build_fpl_spine(con)
    row = con.execute("SELECT birth_date, opta_code FROM dim_player").fetchone()
    assert row == (dt.date(2001, 9, 5), "p223094")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_identity_players.py -v`
Expected: FAIL with `ImportError: cannot import name 'players'`.

- [ ] **Step 3: Write the implementation**

Create `packages/ptb-core/src/ptb/core/identity/players.py`:

```python
"""Resolve a source's player to one `dim_player` row.

FPL's `code` is stable across seasons where `element_id` is reassigned, so it
forms the spine: one dim_player row per code. Understat and FotMob players are
then matched into that spine by three deterministic tiers and a scored fallback.

Where two candidates score too closely to separate, resolution fails and records
the reason instead of guessing -- the same contract as match resolution.
"""
from __future__ import annotations

import logging

import duckdb

from .text import normalize_name

log = logging.getLogger(__name__)

AMBIGUOUS = "ambiguous"


def build_fpl_spine(con: duckdb.DuckDBPyConnection) -> int:
    """Create one dim_player row per distinct FPL code. Returns rows created.

    birth_date and opta_code are taken as the latest non-null across seasons
    rather than the latest season's value: FPL only began publishing them
    recently, so the most recent season is not reliably the populated one.
    """
    rows = con.execute(
        "SELECT code, "
        "       arg_max(trim(coalesce(first_name, '') || ' ' "
        "                    || coalesce(second_name, '')), season) AS canonical_name, "
        "       max(birth_date) AS birth_date, "
        "       max(opta_code)  AS opta_code, "
        "       min(season)     AS first_season, "
        "       max(season)     AS last_season "
        "FROM src_fpl_element WHERE code IS NOT NULL "
        "GROUP BY code ORDER BY code"
    ).fetchall()

    for code, canonical, birth_date, opta_code, first_season, last_season in rows:
        player_id = con.execute("SELECT nextval('seq_player_id')").fetchone()[0]
        con.execute(
            "INSERT INTO dim_player (player_id, canonical_name, normalized_name, "
            "birth_date, fpl_code, opta_code, first_seen_season, last_seen_season) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [player_id, canonical, normalize_name(canonical), birth_date, code,
             opta_code, first_season, last_season],
        )
        con.execute(
            "INSERT INTO map_player_source (player_id, source, source_player_id, "
            "method, confidence) VALUES (?, ?, ?, ?, ?)",
            [player_id, "fpl", str(code), "fpl_code", 1.0],
        )
    return len(rows)
```

- [ ] **Step 4: Export the module**

Replace `packages/ptb-core/src/ptb/core/identity/__init__.py` with:

```python
from . import teams, text, names

from . import matches, players  # noqa: F401,E402

__all__ = ["teams", "text", "names", "matches", "players"]
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_identity_players.py -v`
Expected: PASS, all four.

- [ ] **Step 6: Commit**

```bash
git add packages/ptb-core/src/ptb/core/identity/players.py \
        packages/ptb-core/src/ptb/core/identity/__init__.py \
        tests/test_identity_players.py
git commit -m "Build the FPL player spine"
```

---

### Task 7: Deterministic matching tiers

**Files:**
- Modify: `packages/ptb-core/src/ptb/core/identity/players.py`
- Test: `tests/test_identity_players.py`

**Interfaces:**
- Consumes: `build_fpl_spine` (Task 6), `similarity`/`score_candidate` (Tasks 4–5).
- Produces: the `Candidate` and `Match` NamedTuples, and
  `match_player(candidates: Sequence[Candidate], *, name: str, team_name: Optional[str], birth_date, opta_code) -> Match`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_identity_players.py`:

```python
def _candidate(player_id=1, name="Bukayo Saka", birth_date=None,
               opta_code=None, team_name="Arsenal"):
    return players.Candidate(player_id, name, birth_date, opta_code, team_name)


def test_opta_code_wins_outright():
    result = players.match_player(
        [_candidate(opta_code="p223094"), _candidate(player_id=2, name="Someone Else")],
        name="Totally Different", team_name=None,
        birth_date=None, opta_code="p223094")
    assert (result.player_id, result.method, result.confidence) == (1, "opta_code", 1.00)


def test_name_plus_birth_date_matches():
    result = players.match_player(
        [_candidate(birth_date=dt.date(2001, 9, 5))],
        name="Bukayo Saka", team_name=None,
        birth_date=dt.date(2001, 9, 5), opta_code=None)
    assert (result.method, result.confidence) == ("name_dob", 0.99)


def test_name_plus_team_matches():
    result = players.match_player(
        [_candidate()], name="Bukayo Saka", team_name="Arsenal",
        birth_date=None, opta_code=None)
    assert (result.method, result.confidence) == ("name_team_season", 0.95)


def test_two_players_of_the_same_name_at_the_same_club_are_ambiguous():
    """The homonym case. Two Danny Wards at one club cannot be separated by
    name and team, so the tier must not fire and the fallback must refuse."""
    result = players.match_player(
        [_candidate(player_id=1, name="Danny Ward"),
         _candidate(player_id=2, name="Danny Ward")],
        name="Danny Ward", team_name="Arsenal",
        birth_date=None, opta_code=None)
    assert result.player_id is None
    assert result.reason == players.AMBIGUOUS


def test_two_players_of_the_same_name_split_by_birth_date():
    """Same names, same club, but a birth date separates them cleanly."""
    result = players.match_player(
        [_candidate(player_id=1, name="Danny Ward", birth_date=dt.date(1993, 6, 22)),
         _candidate(player_id=2, name="Danny Ward", birth_date=dt.date(1990, 12, 1))],
        name="Danny Ward", team_name="Arsenal",
        birth_date=dt.date(1990, 12, 1), opta_code=None)
    assert result.player_id == 2
    assert result.method == "name_dob"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_identity_players.py -k "opta or name_plus or same_name" -v`
Expected: FAIL with `AttributeError: module ... has no attribute 'Candidate'`.

- [ ] **Step 3: Write the implementation**

In `players.py`, extend the imports:

```python
import datetime as dt
from typing import List, NamedTuple, Optional, Sequence

from .names import score_candidate, similarity
from .text import normalize_name
```

and add below the `AMBIGUOUS` constant:

```python
SCORE_THRESHOLD = 0.80
SCORE_MARGIN = 0.10


class Candidate(NamedTuple):
    player_id: int
    canonical_name: str
    birth_date: Optional[dt.date]
    opta_code: Optional[str]
    team_name: Optional[str]


class Match(NamedTuple):
    """player_id set means matched. reason set means refuse and record.

    Both unset means no candidate was good enough, which is not a failure --
    it is how a player who has never appeared in FPL enters the dimension.
    """
    player_id: Optional[int] = None
    method: Optional[str] = None
    confidence: Optional[float] = None
    reason: Optional[str] = None
    detail: Optional[str] = None


def match_player(
    candidates: Sequence[Candidate],
    *,
    name: str,
    team_name: Optional[str],
    birth_date: Optional[dt.date],
    opta_code: Optional[str],
) -> Match:
    """Deterministic tiers first, then the scored fallback."""
    normalized = normalize_name(name)

    if opta_code:
        for candidate in candidates:
            if candidate.opta_code and candidate.opta_code == opta_code:
                return Match(candidate.player_id, "opta_code", 1.00)

    if birth_date is not None:
        for candidate in candidates:
            if (candidate.birth_date == birth_date
                    and normalize_name(candidate.canonical_name) == normalized):
                return Match(candidate.player_id, "name_dob", 0.99)

    if team_name is not None:
        normalized_team = normalize_name(team_name)
        exact = [
            candidate.player_id for candidate in candidates
            if normalize_name(candidate.canonical_name) == normalized
            and candidate.team_name is not None
            and normalize_name(candidate.team_name) == normalized_team
        ]
        # More than one means genuine homonyms at one club: the tier cannot
        # separate them, so it declines and leaves it to the fallback.
        if len(exact) == 1:
            return Match(exact[0], "name_team_season", 0.95)

    return _scored_match(
        candidates, name=name, team_name=team_name, birth_date=birth_date)
```

- [ ] **Step 4: Add the scored fallback stub so the tiers are testable**

Also in `players.py`, immediately after `match_player`:

```python
def _scored_match(
    candidates: Sequence[Candidate],
    *,
    name: str,
    team_name: Optional[str],
    birth_date: Optional[dt.date],
) -> Match:
    """Best candidate above the threshold, provided it clears the runner-up.

    The margin is what stops a confident-looking wrong match when two players
    in one block score alike -- exactly the homonym case the tiers declined.
    """
    normalized_team = normalize_name(team_name) if team_name is not None else None

    scored: List[tuple] = []
    for candidate in candidates:
        team_agrees = None
        if normalized_team is not None and candidate.team_name is not None:
            team_agrees = normalize_name(candidate.team_name) == normalized_team
        birth_date_agrees = None
        if birth_date is not None and candidate.birth_date is not None:
            birth_date_agrees = candidate.birth_date == birth_date

        scored.append((
            score_candidate(
                name_similarity=similarity(name, candidate.canonical_name),
                team_agrees=team_agrees,
                birth_date_agrees=birth_date_agrees,
            ),
            candidate.player_id,
        ))

    if not scored:
        return Match()

    scored.sort(reverse=True)
    best_score, best_id = scored[0]
    if best_score < SCORE_THRESHOLD:
        return Match()

    runner_up = scored[1][0] if len(scored) > 1 else 0.0
    if best_score - runner_up < SCORE_MARGIN:
        return Match(
            reason=AMBIGUOUS,
            detail="{:.3f} vs {:.3f}".format(best_score, runner_up),
        )
    return Match(best_id, "scored", best_score)
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_identity_players.py -v`
Expected: PASS, all nine.

- [ ] **Step 6: Commit**

```bash
git add packages/ptb-core/src/ptb/core/identity/players.py tests/test_identity_players.py
git commit -m "Add deterministic tiers and the scored fallback"
```

---

### Task 8: Resolve Understat players

**Files:**
- Modify: `packages/ptb-core/src/ptb/core/identity/players.py`
- Test: `tests/test_identity_players.py`

**Interfaces:**
- Consumes: `build_fpl_spine`, `match_player`, `Candidate`, `Match`.
- Produces: `resolve_understat_players(con) -> int` returning players matched into the spine.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_identity_players.py`:

```python
def _understat_match(con, match_id, season, competition="E0"):
    con.execute(
        "INSERT OR REPLACE INTO src_understat_match (understat_match_id, competition, "
        "season, home_team, away_team, archive_key) VALUES (?, ?, ?, 'Arsenal', 'Wolves', 'k')",
        [match_id, competition, season])


def _understat_shot(con, shot_id, match_id, player_id, player, team):
    con.execute(
        "INSERT OR REPLACE INTO src_understat_shot (understat_shot_id, understat_match_id, "
        "minute, player, player_id, team, home_away, archive_key) "
        "VALUES (?, ?, 10, ?, ?, ?, 'h', 'k')",
        [shot_id, match_id, player, player_id, team])


def test_understat_player_matches_the_fpl_spine(con):
    _fpl_team(con, "2024/25", 1, "Arsenal")
    _fpl_element(con, "2024/25", 11, 223094, "Bukayo", "Saka", 1)
    players.build_fpl_spine(con)

    _understat_match(con, "1001", "2024/25")
    _understat_shot(con, "s1", "1001", "647", "Bukayo Saka", "Arsenal")

    assert players.resolve_understat_players(con) == 1
    row = con.execute(
        "SELECT player_id, method FROM map_player_source "
        "WHERE source = 'understat' AND source_player_id = '647'").fetchone()
    fpl_player_id = con.execute(
        "SELECT player_id FROM dim_player WHERE fpl_code = 223094").fetchone()[0]
    assert row[0] == fpl_player_id
    assert con.execute("SELECT count(*) FROM dim_player").fetchone()[0] == 1


def test_a_shortened_fpl_name_still_matches(con):
    """FPL stores 'Gabriel Jesus'; Understat stores the full legal name."""
    _fpl_team(con, "2024/25", 1, "Arsenal")
    _fpl_element(con, "2024/25", 12, 205651, "Gabriel", "Jesus", 1)
    players.build_fpl_spine(con)

    _understat_match(con, "1001", "2024/25")
    _understat_shot(con, "s1", "1001", "700", "Gabriel Fernando de Jesus", "Arsenal")

    assert players.resolve_understat_players(con) == 1


def test_a_player_absent_from_fpl_gets_a_new_row(con):
    """A La Liga player has no FPL code. Creating the row is how the dimension
    grows beyond the Premier League -- it is not a resolution failure."""
    _understat_match(con, "2001", "2024/25", competition="SP1")
    _understat_shot(con, "s1", "2001", "900", "Robert Lewandowski", "Barcelona")

    assert players.resolve_understat_players(con) == 0
    row = con.execute(
        "SELECT p.canonical_name, m.method FROM dim_player p "
        "JOIN map_player_source m ON m.player_id = p.player_id "
        "WHERE m.source = 'understat'").fetchone()
    assert row == ("Robert Lewandowski", "created")


def test_an_ambiguous_understat_player_is_recorded_not_guessed(con):
    _fpl_team(con, "2024/25", 1, "Arsenal")
    _fpl_element(con, "2024/25", 20, 111111, "Danny", "Ward", 1)
    _fpl_element(con, "2024/25", 21, 222222, "Danny", "Ward", 1)
    players.build_fpl_spine(con)

    _understat_match(con, "1001", "2024/25")
    _understat_shot(con, "s1", "1001", "800", "Danny Ward", "Arsenal")

    assert players.resolve_understat_players(con) == 0
    assert con.execute(
        "SELECT reason FROM unresolved_player WHERE source = 'understat'"
    ).fetchone()[0] == players.AMBIGUOUS
    assert con.execute(
        "SELECT count(*) FROM map_player_source WHERE source = 'understat'"
    ).fetchone()[0] == 0


def test_a_player_in_two_seasons_maps_once(con):
    _fpl_team(con, "2023/24", 1, "Arsenal")
    _fpl_team(con, "2024/25", 1, "Arsenal")
    _fpl_element(con, "2023/24", 7, 223094, "Bukayo", "Saka", 1)
    _fpl_element(con, "2024/25", 11, 223094, "Bukayo", "Saka", 1)
    players.build_fpl_spine(con)

    _understat_match(con, "1001", "2023/24")
    _understat_match(con, "1002", "2024/25")
    _understat_shot(con, "s1", "1001", "647", "Bukayo Saka", "Arsenal")
    _understat_shot(con, "s2", "1002", "647", "Bukayo Saka", "Arsenal")

    players.resolve_understat_players(con)
    assert con.execute(
        "SELECT count(*) FROM map_player_source WHERE source = 'understat'"
    ).fetchone()[0] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_identity_players.py -k understat -v`
Expected: FAIL with `AttributeError: module ... has no attribute 'resolve_understat_players'`.

- [ ] **Step 3: Write the implementation**

Append to `players.py`:

```python
def _fpl_candidates(con: duckdb.DuckDBPyConnection, season: str) -> List[Candidate]:
    """Spine players who appeared in the Premier League in this season.

    Blocking by season keeps each comparison set at roughly 600 players, which
    makes the pairwise cost negligible and removes most homonym risk for free.
    """
    rows = con.execute(
        "SELECT p.player_id, p.canonical_name, p.birth_date, p.opta_code, t.name "
        "FROM dim_player p "
        "JOIN map_player_source m "
        "  ON m.player_id = p.player_id AND m.source = 'fpl' "
        "JOIN src_fpl_element e "
        "  ON CAST(e.code AS VARCHAR) = m.source_player_id AND e.season = ? "
        "JOIN src_fpl_team t ON t.season = e.season AND t.team_id = e.team "
        "ORDER BY p.player_id",
        [season],
    ).fetchall()
    return [Candidate(*row) for row in rows]


def _create_player(
    con: duckdb.DuckDBPyConnection,
    name: str,
    season: str,
    birth_date: Optional[dt.date] = None,
) -> int:
    player_id = con.execute("SELECT nextval('seq_player_id')").fetchone()[0]
    con.execute(
        "INSERT INTO dim_player (player_id, canonical_name, normalized_name, "
        "birth_date, first_seen_season, last_seen_season) VALUES (?, ?, ?, ?, ?, ?)",
        [player_id, name, normalize_name(name), birth_date, season, season],
    )
    return player_id


def _resolve_source_players(con, source: str, rows: Sequence[tuple]) -> int:
    """rows: (source_player_id, name, competition, season, team_name).

    Returns the number matched into the spine.

    Blocking is by (competition, season), not season alone. The spine is
    Premier League only, so comparing a La Liga player against it could produce
    a confident wrong match on a similar name -- outside E0 there is simply no
    candidate set, and the player becomes a new dim_player row.

    Rows arrive ordered by season, so a player active across several seasons is
    decided by their earliest appearance and skipped thereafter.
    """
    candidate_cache = {}
    seen = set()
    matched = 0

    for source_player_id, name, competition, season, team_name in rows:
        key = str(source_player_id)
        if key in seen or not name:
            continue
        seen.add(key)

        block = (competition, season)
        if block not in candidate_cache:
            candidate_cache[block] = (
                _fpl_candidates(con, season) if competition == "E0" else []
            )

        result = match_player(
            candidate_cache[block],
            name=name, team_name=team_name, birth_date=None, opta_code=None,
        )

        if result.reason is not None:
            con.execute(
                "INSERT OR REPLACE INTO unresolved_player "
                "(source, source_player_id, reason, detail) VALUES (?, ?, ?, ?)",
                [source, key, result.reason, result.detail],
            )
            continue

        if result.player_id is None:
            player_id, method, confidence = _create_player(con, name, season), "created", 1.0
        else:
            player_id = result.player_id
            method, confidence = result.method, result.confidence
            matched += 1

        con.execute(
            "INSERT OR REPLACE INTO map_player_source (player_id, source, "
            "source_player_id, method, confidence) VALUES (?, ?, ?, ?, ?)",
            [player_id, source, key, method, confidence],
        )
    return matched


def resolve_understat_players(con: duckdb.DuckDBPyConnection) -> int:
    """Match Understat shot-takers into dim_player. Idempotent.

    A player can move club mid-season, so the modal team across their shots is
    used rather than any single shot's.
    """
    rows = con.execute(
        "SELECT s.player_id, mode(s.player) AS player_name, m.competition, m.season, "
        "       mode(s.team) AS team_name "
        "FROM src_understat_shot s "
        "JOIN src_understat_match m ON m.understat_match_id = s.understat_match_id "
        "WHERE s.player_id IS NOT NULL "
        "GROUP BY s.player_id, m.competition, m.season "
        "ORDER BY m.season, s.player_id"
    ).fetchall()
    return _resolve_source_players(con, "understat", rows)
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_identity_players.py -v`
Expected: PASS, all fourteen.

- [ ] **Step 5: Commit**

```bash
git add packages/ptb-core/src/ptb/core/identity/players.py tests/test_identity_players.py
git commit -m "Resolve Understat players into the spine"
```

---

### Task 9: Resolve FotMob players

FotMob player rows carry `fotmob_team_id` but no team name — the name lives on
`src_fotmob_team_stat`, so the two are joined.

**Files:**
- Modify: `packages/ptb-core/src/ptb/core/identity/players.py`
- Test: `tests/test_identity_players.py`

**Interfaces:**
- Consumes: `_resolve_source_players` (Task 8).
- Produces: `resolve_fotmob_players(con) -> int`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_identity_players.py`:

```python
def _fotmob_player(con, player_id, name, team_id, season="2024/25",
                   league_id=47, stat="expected_goals"):
    con.execute(
        "INSERT OR REPLACE INTO src_fotmob_player_stat (league_id, season, stat_name, "
        "fotmob_player_id, fotmob_team_id, player_name, value, archive_key) "
        "VALUES (?, ?, ?, ?, ?, ?, 1.0, 'k')",
        [league_id, season, stat, player_id, team_id, name])


def _fotmob_team(con, team_id, name, season="2024/25", league_id=47):
    con.execute(
        "INSERT OR REPLACE INTO src_fotmob_team_stat (league_id, season, stat_name, "
        "fotmob_team_id, team_name, value, archive_key) "
        "VALUES (?, ?, 'expected_goals', ?, ?, 1.0, 'k')",
        [league_id, season, team_id, name])


def test_fotmob_player_matches_the_fpl_spine(con):
    _fpl_team(con, "2024/25", 1, "Arsenal")
    _fpl_element(con, "2024/25", 11, 223094, "Bukayo", "Saka", 1)
    players.build_fpl_spine(con)

    _fotmob_team(con, 9825, "Arsenal")
    _fotmob_player(con, 737066, "Bukayo Saka", 9825)

    assert players.resolve_fotmob_players(con) == 1
    fpl_player_id = con.execute(
        "SELECT player_id FROM dim_player WHERE fpl_code = 223094").fetchone()[0]
    assert con.execute(
        "SELECT player_id FROM map_player_source WHERE source = 'fotmob'"
    ).fetchone()[0] == fpl_player_id


def test_fotmob_player_appearing_in_many_stat_boards_maps_once(con):
    """One player appears on every leaderboard for their league and season."""
    _fpl_team(con, "2024/25", 1, "Arsenal")
    _fpl_element(con, "2024/25", 11, 223094, "Bukayo", "Saka", 1)
    players.build_fpl_spine(con)

    _fotmob_team(con, 9825, "Arsenal")
    _fotmob_player(con, 737066, "Bukayo Saka", 9825, stat="expected_goals")
    _fotmob_player(con, 737066, "Bukayo Saka", 9825, stat="goals")
    _fotmob_player(con, 737066, "Bukayo Saka", 9825, stat="assists")

    assert players.resolve_fotmob_players(con) == 1
    assert con.execute(
        "SELECT count(*) FROM map_player_source WHERE source = 'fotmob'"
    ).fetchone()[0] == 1


def test_championship_players_are_resolved_separately(con):
    """League 48 is the Championship. Its players have no Premier League FPL
    candidate, so they enter the dimension as new rows."""
    _fotmob_team(con, 8678, "Leeds", league_id=48)
    _fotmob_player(con, 999001, "Some Championship Player", 8678, league_id=48)

    assert players.resolve_fotmob_players(con) == 0
    assert con.execute(
        "SELECT count(*) FROM map_player_source WHERE source = 'fotmob'"
    ).fetchone()[0] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_identity_players.py -k fotmob -v`
Expected: FAIL with `AttributeError: module ... has no attribute 'resolve_fotmob_players'`.

- [ ] **Step 3: Write the implementation**

Append to `players.py`:

```python
def resolve_fotmob_players(con: duckdb.DuckDBPyConnection) -> int:
    """Match FotMob leaderboard players into dim_player. Idempotent.

    A player appears on every stat board for their league and season, so rows
    are collapsed to one per (player, season). Team names live on the team
    boards rather than the player rows, hence the join.
    """
    rows = con.execute(
        "SELECT p.fotmob_player_id, mode(p.player_name) AS player_name, "
        "       CASE p.league_id WHEN 47 THEN 'E0' WHEN 48 THEN 'E1' END AS competition, "
        "       p.season, mode(t.team_name) AS team_name "
        "FROM src_fotmob_player_stat p "
        "LEFT JOIN src_fotmob_team_stat t "
        "  ON t.league_id = p.league_id AND t.season = p.season "
        " AND t.fotmob_team_id = p.fotmob_team_id "
        "WHERE p.fotmob_player_id IS NOT NULL "
        "GROUP BY p.fotmob_player_id, p.league_id, p.season "
        "ORDER BY p.season, p.fotmob_player_id"
    ).fetchall()
    return _resolve_source_players(con, "fotmob", rows)
```

FotMob league 47 is the Premier League and 48 the Championship — the two the
source ingests, per `LEAGUES` in `sources/fotmob.py`. Championship players need
no special handling beyond that mapping: their block is `E1`, which has no FPL
candidate set, so they become new `dim_player` rows.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_identity_players.py -v`
Expected: PASS, all seventeen.

- [ ] **Step 5: Commit**

```bash
git add packages/ptb-core/src/ptb/core/identity/players.py tests/test_identity_players.py
git commit -m "Resolve FotMob players into the spine"
```

---

### Task 10: Overrides and the top-level resolver

`resolve_players` is the single entry point: it wipes and rebuilds every player
table, which is what makes the map re-derivable rather than a hand-maintained
artefact that must survive rebuilds.

**Files:**
- Modify: `packages/ptb-core/src/ptb/core/identity/players.py`
- Create: `packages/ptb-core/src/ptb/core/identity/player_overrides.yaml`
- Test: `tests/test_identity_players.py`

**Interfaces:**
- Consumes: everything from Tasks 6, 8, 9.
- Produces: `resolve_players(con) -> int`, `apply_overrides(con, path=None) -> int`, `OVERRIDES_PATH`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_identity_players.py`:

```python
def test_resolve_players_runs_every_source(con):
    _fpl_team(con, "2024/25", 1, "Arsenal")
    _fpl_element(con, "2024/25", 11, 223094, "Bukayo", "Saka", 1)
    _understat_match(con, "1001", "2024/25")
    _understat_shot(con, "s1", "1001", "647", "Bukayo Saka", "Arsenal")
    _fotmob_team(con, 9825, "Arsenal")
    _fotmob_player(con, 737066, "Bukayo Saka", 9825)

    players.resolve_players(con)

    assert con.execute("SELECT count(*) FROM dim_player").fetchone()[0] == 1
    sources = {r[0] for r in con.execute(
        "SELECT DISTINCT source FROM map_player_source").fetchall()}
    assert sources == {"fpl", "understat", "fotmob"}


def test_resolve_players_is_idempotent(con):
    """Two rebuilds must produce byte-identical tables, including player_ids --
    that is what makes the warehouse disposable."""
    _fpl_team(con, "2024/25", 1, "Arsenal")
    _fpl_element(con, "2024/25", 11, 223094, "Bukayo", "Saka", 1)
    _understat_match(con, "1001", "2024/25")
    _understat_shot(con, "s1", "1001", "647", "Bukayo Saka", "Arsenal")

    players.resolve_players(con)
    first = con.execute(
        "SELECT * FROM dim_player ORDER BY player_id").fetchall()
    first_map = con.execute(
        "SELECT * FROM map_player_source ORDER BY source, source_player_id").fetchall()

    players.resolve_players(con)
    assert con.execute(
        "SELECT * FROM dim_player ORDER BY player_id").fetchall() == first
    assert con.execute(
        "SELECT * FROM map_player_source ORDER BY source, source_player_id"
    ).fetchall() == first_map


def test_an_override_forces_a_mapping(con, tmp_path):
    _fpl_team(con, "2024/25", 1, "Arsenal")
    _fpl_element(con, "2024/25", 11, 223094, "Bukayo", "Saka", 1)
    players.build_fpl_spine(con)

    overrides = tmp_path / "overrides.yaml"
    overrides.write_text("understat:\n  '12345': 223094\n", encoding="utf-8")

    assert players.apply_overrides(con, overrides) == 1
    row = con.execute(
        "SELECT method, confidence FROM map_player_source "
        "WHERE source = 'understat' AND source_player_id = '12345'").fetchone()
    assert row == ("override", 1.0)


def test_an_override_can_force_a_non_match(con, tmp_path):
    """A null override is the only way to undo a confidently wrong scored
    match without loosening the threshold for everyone."""
    _fpl_team(con, "2024/25", 1, "Arsenal")
    _fpl_element(con, "2024/25", 11, 223094, "Bukayo", "Saka", 1)
    players.build_fpl_spine(con)
    _understat_match(con, "1001", "2024/25")
    _understat_shot(con, "s1", "1001", "647", "Bukayo Saka", "Arsenal")
    players.resolve_understat_players(con)

    overrides = tmp_path / "overrides.yaml"
    overrides.write_text("understat:\n  '647': null\n", encoding="utf-8")
    players.apply_overrides(con, overrides)

    assert con.execute(
        "SELECT count(*) FROM map_player_source WHERE source = 'understat'"
    ).fetchone()[0] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_identity_players.py -k "resolve_players or override" -v`
Expected: FAIL with `AttributeError: module ... has no attribute 'resolve_players'`.

- [ ] **Step 3: Create the overrides file**

Create `packages/ptb-core/src/ptb/core/identity/player_overrides.yaml`:

```yaml
# Manual corrections to derived player identity. Applied last, always wins.
#
# Keyed by source, then that source's own player id (quoted, always a string).
# The value is the FPL `code` to bind to -- a stable, human-findable number --
# rather than the surrogate player_id, which changes between rebuilds.
#
# A null value forces a NON-match: the source player is left unmapped. That is
# the only way to undo a confidently wrong scored match without loosening the
# threshold for every other player.
#
# understat:
#   "647": 223094
#   "1899": null
# fotmob:
#   "737066": 223094
```

- [ ] **Step 4: Write the implementation**

In `players.py`, extend the imports:

```python
from pathlib import Path

import yaml
```

and append:

```python
OVERRIDES_PATH = Path(__file__).with_name("player_overrides.yaml")


def apply_overrides(con: duckdb.DuckDBPyConnection, path=None) -> int:
    """Apply the committed manual corrections. Returns entries applied."""
    target = Path(path) if path is not None else OVERRIDES_PATH
    if not target.is_file():
        return 0

    data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    applied = 0

    for source, entries in data.items():
        for source_player_id, fpl_code in (entries or {}).items():
            key = str(source_player_id)
            con.execute(
                "DELETE FROM map_player_source WHERE source = ? AND source_player_id = ?",
                [source, key])
            con.execute(
                "DELETE FROM unresolved_player WHERE source = ? AND source_player_id = ?",
                [source, key])

            if fpl_code is None:
                applied += 1
                continue

            row = con.execute(
                "SELECT player_id FROM dim_player WHERE fpl_code = ?", [fpl_code]
            ).fetchone()
            if row is None:
                log.warning(
                    "override %s/%s references unknown fpl code %s", source, key, fpl_code)
                continue

            con.execute(
                "INSERT INTO map_player_source (player_id, source, source_player_id, "
                "method, confidence) VALUES (?, ?, ?, 'override', 1.0)",
                [row[0], source, key])
            applied += 1
    return applied


def resolve_players(con: duckdb.DuckDBPyConnection) -> int:
    """Rebuild player identity from the src_ tables. Returns spine matches.

    Every player table is wiped first and the sequence restarted, so two runs
    produce identical tables down to the player_ids. That is what makes the map
    re-derivable rather than an artefact that has to survive rebuilds.

    This runs once after every source has loaded, never per-source: matching is
    inherently cross-source, and the FPL spine must exist before anything can be
    matched into it.
    """
    con.execute("DELETE FROM map_player_source")
    con.execute("DELETE FROM unresolved_player")
    con.execute("DELETE FROM dim_player")
    con.execute("CREATE OR REPLACE SEQUENCE seq_player_id START 1")

    build_fpl_spine(con)
    matched = resolve_understat_players(con)
    matched += resolve_fotmob_players(con)
    apply_overrides(con)
    return matched
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_identity_players.py -v`
Expected: PASS, all twenty-one.

- [ ] **Step 6: Commit**

```bash
git add packages/ptb-core/src/ptb/core/identity/players.py \
        packages/ptb-core/src/ptb/core/identity/player_overrides.yaml \
        tests/test_identity_players.py
git commit -m "Add player overrides and the top-level resolver"
```

---

### Task 11: Wire into rebuild and coverage

**Files:**
- Modify: `packages/ptb-core/src/ptb/core/warehouse/load.py`
- Modify: `packages/ptb-core/src/ptb/core/cli.py`
- Test: `tests/test_identity_players.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `resolve_players` (Task 10).
- Produces: player resolution as part of `load.rebuild`; `unresolved_player` reported by `ptb coverage`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_identity_players.py`:

```python
def test_rebuild_resolves_players_after_every_source_loads(con, tmp_path):
    """Player matching is cross-source, so it cannot run inside the per-source
    branches the way match resolution does -- the spine must already exist."""
    from ptb.core.archive import LocalBackend, RawArchive
    from ptb.core.warehouse import load

    archive = RawArchive(LocalBackend(tmp_path / "archive"))
    payload = {
        "source": "fpl", "endpoint": "bootstrap", "season": "2024/25",
        "data": {
            "teams": [{"id": 1, "name": "Arsenal", "short_name": "ARS"}],
            "element_types": [], "events": [],
            "elements": [{"id": 11, "code": 223094, "web_name": "Saka",
                          "first_name": "Bukayo", "second_name": "Saka",
                          "team": 1, "element_type": 3}],
        },
    }
    archive.write("fpl", "bootstrap", payload)

    load.rebuild(con, archive)
    assert con.execute("SELECT count(*) FROM dim_player").fetchone()[0] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_identity_players.py -k rebuild -v`
Expected: FAIL — `dim_player` is empty because nothing calls the resolver.

- [ ] **Step 3: Wire the resolver into rebuild**

In `packages/ptb-core/src/ptb/core/warehouse/load.py`, inside `rebuild`, replace
the final `return written` with:

```python
    # Player matching is inherently cross-source -- an Understat player is
    # matched against the FPL spine -- so it runs once after every source has
    # loaded, not inside the per-source branches above.
    if written:
        from ..identity import players as identity_players

        matched = identity_players.resolve_players(con)
        log.info("matched %d source players into dim_player", matched)

    return written
```

- [ ] **Step 4: Report unresolved players in coverage**

In `packages/ptb-core/src/ptb/core/cli.py`, inside `_cmd_coverage`, extend the
query block. After the existing `unresolved` assignment add:

```python
        unresolved_players = con.execute(
            "SELECT count(*) FROM unresolved_player").fetchone()[0]
        players_mapped = con.execute(
            "SELECT count(*) FROM map_player_source").fetchone()[0]
```

and after the existing `if unresolved:` block add:

```python
    if players_mapped:
        print("\n{} player mapping(s) across sources".format(players_mapped))
    if unresolved_players:
        print("{} unresolved player(s) -- see the unresolved_player table".format(
            unresolved_players))
```

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS, every test in the repo.

- [ ] **Step 6: Commit**

```bash
git add packages/ptb-core/src/ptb/core/warehouse/load.py \
        packages/ptb-core/src/ptb/core/cli.py tests/test_identity_players.py
git commit -m "Resolve players during rebuild and report them in coverage"
```

---

### Task 12: Validation against the sacked-in-the-morning baseline

The success bar from the spec, made runnable. This is a one-off migration check,
so it ships as a script rather than production code.

**Files:**
- Create: `scripts/compare_player_map.py`

**Interfaces:**
- Consumes: a built ptb warehouse and the fpl-app warehouse.
- Produces: a coverage and conflict report on stdout.

- [ ] **Step 1: Write the script**

Create `scripts/compare_player_map.py`:

```python
"""Compare ptb's derived player map against sacked-in-the-morning's.

sacked-in-the-morning's map_player_external is itself auto-derived by name
matching -- every row carries an "auto: ... name match" note -- so it is a
baseline to beat, not a source of truth. Coverage below it means the new
matcher is worse; conflicts are the rows worth reading by hand.

Coverage is computed over dim_player WHERE fpl_code IS NOT NULL. ptb's
dimension also holds Big-5 players who never appear in FPL, and counting those
would make the two percentages incomparable.

Usage:
    uv run python scripts/compare_player_map.py PTB_DB SITM_DB
"""
from __future__ import annotations

import sys

import duckdb

BASELINE = {"understat": 67.0, "fotmob": 77.0}


def main(argv):
    if len(argv) != 3:
        print(__doc__)
        return 2
    ptb_path, sitm_path = argv[1], argv[2]

    con = duckdb.connect(ptb_path, read_only=True)
    con.execute("ATTACH ? AS sitm (READ_ONLY)", [sitm_path])

    spine = con.execute(
        "SELECT count(*) FROM dim_player WHERE fpl_code IS NOT NULL").fetchone()[0]
    if not spine:
        print("no FPL spine in the warehouse -- run `ptb rebuild` first")
        return 1

    print("FPL spine players: {}".format(spine))
    print("\nCOVERAGE (of the FPL spine)")
    for source, baseline in sorted(BASELINE.items()):
        mapped = con.execute(
            "SELECT count(DISTINCT m.player_id) FROM map_player_source m "
            "JOIN dim_player p ON p.player_id = m.player_id "
            "WHERE m.source = ? AND p.fpl_code IS NOT NULL", [source]
        ).fetchone()[0]
        pct = 100.0 * mapped / spine
        verdict = "PASS" if pct > baseline else "FAIL"
        print("  {:<10} {:>5} / {:<5} {:>6.1f}%   baseline {:.0f}%   {}".format(
            source, mapped, spine, pct, baseline, verdict))

    total = con.execute("SELECT count(*) FROM dim_player").fetchone()[0]
    print("\n  (whole dimension: {} players, incl. non-FPL)".format(total))

    print("\nAGREEMENT WITH THE BASELINE (understat)")
    rows = con.execute(
        "WITH ours AS ("
        "  SELECT p.fpl_code, m.source_player_id AS understat_id "
        "  FROM map_player_source m "
        "  JOIN dim_player p ON p.player_id = m.player_id "
        "  WHERE m.source = 'understat' AND p.fpl_code IS NOT NULL"
        "), theirs AS ("
        "  SELECT player_code AS fpl_code, understat_id "
        "  FROM sitm.map_player_external WHERE understat_id IS NOT NULL"
        ") "
        "SELECT "
        "  count(*) FILTER (WHERE o.understat_id = t.understat_id) AS agree, "
        "  count(*) FILTER (WHERE t.fpl_code IS NULL)              AS ours_only, "
        "  count(*) FILTER (WHERE o.fpl_code IS NULL)              AS theirs_only, "
        "  count(*) FILTER (WHERE o.understat_id IS NOT NULL "
        "                    AND t.understat_id IS NOT NULL "
        "                    AND o.understat_id <> t.understat_id) AS conflict "
        "FROM ours o FULL OUTER JOIN theirs t ON t.fpl_code = o.fpl_code"
    ).fetchone()
    for label, value in zip(("agree", "ours only", "theirs only", "CONFLICT"), rows):
        print("  {:<12} {}".format(label, value))

    if rows[3]:
        print("\nCONFLICTS -- read these by hand")
        for code, ours, theirs in con.execute(
            "WITH ours AS ("
            "  SELECT p.fpl_code, p.canonical_name, m.source_player_id AS understat_id "
            "  FROM map_player_source m "
            "  JOIN dim_player p ON p.player_id = m.player_id "
            "  WHERE m.source = 'understat' AND p.fpl_code IS NOT NULL"
            ") "
            "SELECT o.canonical_name, o.understat_id, t.understat_id "
            "FROM ours o JOIN sitm.map_player_external t ON t.player_code = o.fpl_code "
            "WHERE t.understat_id IS NOT NULL AND t.understat_id <> o.understat_id "
            "ORDER BY o.canonical_name LIMIT 50"
        ).fetchall():
            print("  {:<28} ours={:<10} theirs={}".format(code, ours, theirs))

    print("\nAUDIT SAMPLE -- 50 scored-tier matches, check by hand for precision")
    print("Bar: >= 98%, i.e. at most one wrong. Deterministic tiers are excluded;")
    print("they are not where wrong answers come from.\n")
    for name, source, sid, confidence in con.execute(
        "SELECT p.canonical_name, m.source, m.source_player_id, m.confidence "
        "FROM map_player_source m "
        "JOIN dim_player p ON p.player_id = m.player_id "
        "WHERE m.method = 'scored' "
        "USING SAMPLE 50 ROWS (reservoir, 42)"
    ).fetchall():
        print("  {:<28} {:<10} {:<12} {:.3f}".format(name, source, sid, confidence))

    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
```

- [ ] **Step 2: Run it against a real warehouse**

```bash
uv run python scripts/compare_player_map.py data/ptb.duckdb ../fpl-app/data/fpl.duckdb
```

`data/ptb.duckdb` is `config.DB_PATH`: the repo-root `data/` directory, unless
`PTB_DB_PATH` or `PTB_DATA_DIR` is set. If the warehouse has not been built yet,
run `ptb ingest all` then `ptb rebuild` first.

Expected: both coverage lines report PASS. If either reports FAIL, tune
`SCORE_THRESHOLD` and `SCORE_MARGIN` in `players.py` and re-run — that is what
those constants are for.

- [ ] **Step 3: Hand-audit the sample**

Work through the 50 printed scored-tier matches. For each, confirm the ptb
player and the source player are the same footballer. At most one may be wrong.

If precision fails, raise `SCORE_THRESHOLD` and re-run both this and the coverage
check — the two move in opposite directions, which is the trade-off the audit
exists to settle. If the scored tier cannot clear both bars, delete
`_scored_match` and its call, leaving the deterministic cascade. That outcome is
a valid result, not a failure of the plan.

- [ ] **Step 4: Commit**

```bash
git add scripts/compare_player_map.py
git commit -m "Add the player map validation harness"
```

---

## Definition of done

- [ ] `uv run pytest -q` passes with no failures.
- [ ] `uv run python scripts/compare_player_map.py ...` reports PASS on both coverage lines.
- [ ] The 50-row audit sample has been read by hand and shows at most one wrong match.
- [ ] Conflicts against the baseline have been read, and any genuine errors are recorded in `player_overrides.yaml`.
- [ ] `ptb rebuild` run twice produces identical `dim_player` and `map_player_source` contents.
- [ ] The "permanent manual liability" paragraph in `docs/superpowers/specs/2026-08-02-universal-soccer-data-layer-design.md` has been amended — `map_player_external` was already auto-derived.
