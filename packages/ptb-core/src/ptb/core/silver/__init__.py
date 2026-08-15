"""The silver layer: one module per source, plus the identity built on top.

Each source module owns its whole pipeline and nothing outside it does:

    ingest(...)                network -> archive, returns written keys
    load(con, payload, key)    one archived payload -> its src_ tables
    <name>.sql                 the DDL for those tables, applied at connect
    resolve_matches(con)       its rows -> dim_match      (where it has any)
    resolve_players(con)       its rows -> dim_player     (where it has any)

`ingest` never touches the warehouse and `load` never touches the network.
That split, not the directory layout, is what keeps the warehouse
disposable: delete `data/` and a replay of the archive reproduces it.

The `src_*` tables those loads write are source-faithful -- one table per
source per entity, never merged and never reconciled. Where two sources
disagree, both values survive and a gold view picks one.

Adding a source means adding `<name>.py` and `<name>.sql` here and one entry
to `_MODULES`. Everything else -- schema application, replay, identity
resolution -- picks it up from there. (`ptb coverage` is the one exception:
its SQL is hand-written and needs a UNION ALL added by hand.)

The non-source modules here are shared machinery: `names` (normalization and
comparison), `teams`/`matches`/`players` (conformed dimensions and the
resolution each source calls into), and `coerce` (defensive numeric parsing).
"""
from . import asa, draftkings, footballdata, fotmob, fpl, understat, vaastav

_MODULES = (asa, draftkings, footballdata, fotmob, fpl, understat, vaastav)

SOURCES = {module.NAME: module for module in _MODULES}


class UnknownSourceError(KeyError):
    pass


def source(name: str):
    """The module for a source name, so the CLI can dispatch on a string."""
    try:
        return SOURCES[name]
    except KeyError:
        raise UnknownSourceError(
            "unknown source {!r}; available: {}".format(name, ", ".join(source_names()))
        ) from None


def source_names():
    return sorted(SOURCES)
