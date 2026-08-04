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
