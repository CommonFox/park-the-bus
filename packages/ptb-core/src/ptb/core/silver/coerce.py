"""Defensive numeric coercion, shared by every loader.

Sources send numbers as strings, as empty strings, and as nulls
interchangeably, and the same field changes type between eras. A single
stray value must never abort a rebuild of the whole archive, so anything
unparseable becomes None rather than raising -- absence and garbage are
both simply "no value here".
"""
from __future__ import annotations

from typing import Any, Optional


def as_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def as_int(value: Any) -> Optional[int]:
    """Via float, so '3.0' and 3.7 both land on an int rather than raising."""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None
