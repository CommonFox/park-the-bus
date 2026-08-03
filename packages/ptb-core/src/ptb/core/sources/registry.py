"""Name -> source lookup, so the CLI can dispatch on a string."""
from __future__ import annotations

from typing import Dict, List, Type

_REGISTRY: Dict[str, Type] = {}


class UnknownSourceError(KeyError):
    pass


def register_source(cls: Type) -> Type:
    name = getattr(cls, "name", None)
    if not name or not isinstance(name, str):
        raise ValueError("{} needs a non-empty string `name`".format(cls.__name__))
    if name in _REGISTRY:
        raise ValueError("source {!r} is already registered".format(name))
    _REGISTRY[name] = cls
    return cls


def get_source(name: str):
    try:
        return _REGISTRY[name]()
    except KeyError:
        raise UnknownSourceError(
            "unknown source {!r}; available: {}".format(name, ", ".join(list_sources()))
        ) from None


def list_sources() -> List[str]:
    return sorted(_REGISTRY)
