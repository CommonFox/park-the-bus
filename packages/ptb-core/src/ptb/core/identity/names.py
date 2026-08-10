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
