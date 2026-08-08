"""Player name comparison primitives. Pure functions, no database.

Sources write the same footballer very differently. Understat has
'Gabriel Fernando de Jesus'; FPL's web_name is 'Jesus'. Comparing one canonical
string against another therefore loses matches that are obvious to a human, so
each name expands into a small set of variants and similarity is the best score
over the cross product.
"""
from __future__ import annotations

from typing import Optional, Set

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
