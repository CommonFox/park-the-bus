"""Name normalization and comparison. Pure functions, no database.

Two problems, one module. Sources spell the same club a dozen ways -- 'Man
United', 'Man Utd', 'Manchester Utd' -- and differ on diacritics, so
normalizing to a comparable key is the first half of resolving them (the
alias table in teams.py is the second).

Players are worse. Understat has 'Gabriel Fernando de Jesus'; FPL's
web_name is 'Jesus'. Comparing one canonical string against another
therefore loses matches that are obvious to a human, so each name expands
into a small set of variants and similarity is the best score over the
cross product.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Optional, Set

from rapidfuzz.distance import JaroWinkler

_PUNCTUATION = re.compile(r"[^\w\s]", flags=re.UNICODE)
_WHITESPACE = re.compile(r"\s+")

# NFKD decomposes accented letters into a base plus a combining mark, but these
# are distinct letters rather than accented ones and survive it untouched.
_SPECIAL = str.maketrans({
    "ø": "o", "đ": "d", "ð": "d", "ł": "l", "ß": "ss",
    "æ": "ae", "œ": "oe", "þ": "th", "ı": "i",
})


def strip_diacritics(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize_name(value: str) -> str:
    """Lowercase, de-accent, drop punctuation, collapse whitespace."""
    folded = strip_diacritics(value).lower().translate(_SPECIAL)
    folded = _PUNCTUATION.sub("", folded)
    return _WHITESPACE.sub(" ", folded).strip()


def name_variants(full_name: str) -> Set[str]:
    """Normalized forms a source might plausibly use for this player.

    A two-token name also contributes its swapped order. Some sources --
    FPL's first_name/second_name split is the confirmed case, for players
    whose registered name is East Asian -- disagree with the rest on which
    token is the given name and which is the surname, so 'Endo Wataru' and
    'Wataru Endo' otherwise share no variant at all and Jaro-Winkler scores
    the reversed pair too low to clear the match threshold. This is safe
    under the same trust rule as every other variant: it only decides a
    match when it equals the *other* side's actual full name (see
    `similarity`), and two unrelated players who happen to be each other's
    reversed given-name/surname pair is not a coincidence truncation risk
    -- unlike a shared prefix or surname, nothing is being discarded here.
    """
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
    if len(parts) == 2:
        variants.add("{} {}".format(parts[1], parts[0]))
    return variants


def similarity(left: str, right: str) -> float:
    """1.0 if a shared variant is one side's own full name, else Jaro-Winkler
    over the full names.

    A shared variant is only trustworthy when it equals one side's actual
    full normalized name -- the Gabriel Jesus / Jesus case, where Understat's
    full legal name and FPL's bare-surname web_name genuinely refer to the
    same string once one side is truncated. It is not trustworthy when BOTH
    sides are independently truncating down to the same short form: Andre
    Gray and Archie Gray both reduce to 'gray' and 'a gray', which is a
    coincidence of two different full names, not evidence they match. The
    same reasoning rules out fuzzy comparison between two bare truncated
    variants -- Jaro-Winkler is generous with short strings ('Ings' vs
    'Mings' scores 0.93) -- so fuzziness is reserved for the full names.
    """
    left_norm = normalize_name(left)
    right_norm = normalize_name(right)
    if not left_norm or not right_norm:
        return 0.0

    common = name_variants(left) & name_variants(right)
    if common and (left_norm in common or right_norm in common):
        return 1.0
    return JaroWinkler.similarity(left_norm, right_norm)


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
