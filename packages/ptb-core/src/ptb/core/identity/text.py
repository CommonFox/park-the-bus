"""Name normalization, shared by team and (later) player resolution.

Sources spell the same club a dozen ways -- 'Man United', 'Man Utd',
'Manchester Utd' -- and differ on diacritics. Normalizing to a comparable
key is the first half of resolving them; the alias table is the second.
"""
from __future__ import annotations

import re
import unicodedata

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
