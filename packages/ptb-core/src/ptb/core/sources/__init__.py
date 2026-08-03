from .base import Source
from .registry import UnknownSourceError, get_source, list_sources, register_source

__all__ = ["Source", "register_source", "get_source", "list_sources", "UnknownSourceError"]

from . import footballdata  # noqa: F401,E402  -- imported for registration side effect
from . import understat  # noqa: F401,E402  -- imported for registration side effect
from . import asa  # noqa: F401,E402  -- imported for registration side effect
from . import fpl  # noqa: F401,E402  -- imported for registration side effect
