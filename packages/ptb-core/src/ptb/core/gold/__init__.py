"""The gold layer: conformed views over the silver tables. Nothing here yet.

Silver keeps every source's numbers side by side and refuses to choose
between them. Gold is where a choice gets made once, explicitly, and every
downstream project (FPL research, GAR, WAR, NWSL) reads the result instead
of re-deriving it -- one `v_*` view per measure, plus a
`(measure, source, priority)` precedence table so "xg resolves Understat
before FotMob" is data rather than a hardcoded branch in a loader.

The same rule as silver applies, one layer up: a gold module reads tables
that are already loaded and writes a view or table back. No fetch step, no
network, no reaching past silver into the archive.

One module per view, mirroring silver's one-module-per-source. The first
one identified is `v_match_odds`: de-vig `src_footballdata_odds` (settled)
and `src_draftkings_odds` (forward) into probabilities, with source
precedence -- which is what a fixture-difficulty solver would read.
"""
