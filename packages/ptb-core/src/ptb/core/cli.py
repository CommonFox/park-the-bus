"""The `ptb` command.

Subcommands are added by later tasks. The split that matters: `ingest` writes
only to the archive, `rebuild` reads only from it. Nothing does both.
"""
from __future__ import annotations

import argparse
import logging
import sys
from typing import Optional, Sequence

from . import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ptb", description="Soccer data collection and warehouse")
    # Handled manually in main() so the whole CLI returns int exit codes rather
    # than raising SystemExit from argparse's built-in version action.
    parser.add_argument("--version", action="store_true", help="print version and exit")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    ingest = subparsers.add_parser("ingest", help="fetch from a source into the archive")
    ingest.add_argument("source", help="source name, or 'all'")
    ingest.add_argument("--competition", help="comma-separated competition codes")
    ingest.add_argument("--season", help="comma-separated seasons, e.g. 2024/25,2023/24")
    ingest.add_argument("--refetch", action="store_true",
                        help="re-fetch payloads already in the archive")

    rebuild = subparsers.add_parser("rebuild", help="replay the archive into the warehouse")
    rebuild.add_argument("--source", help="comma-separated source names")

    subparsers.add_parser("coverage", help="competition x season x source grid")

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.version:
        print(__version__)
        return 0

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    if not args.command:
        parser.print_usage()
        return 2

    handler = _HANDLERS.get(args.command)
    if handler is None:
        parser.error("unknown command: {}".format(args.command))
    return handler(args)


def _cmd_ingest(args) -> int:
    from .archive import RawArchive
    from .sources import get_source, list_sources
    from .sources.footballdata import parse_season

    names = list_sources() if args.source == "all" else [args.source]
    archive = RawArchive()

    options = {}
    if args.competition:
        options["competitions"] = [c.strip() for c in args.competition.split(",")]
    if args.season:
        options["seasons"] = [parse_season(s.strip()) for s in args.season.split(",")]
    if args.refetch:
        options["refetch"] = True

    total = 0
    for name in names:
        keys = get_source(name).ingest(archive, **options)
        print("{}: archived {} payload(s)".format(name, len(keys)))
        total += len(keys)

    return 0 if total else 1


def _cmd_rebuild(args) -> int:
    from .archive import RawArchive
    from .warehouse import db, load

    sources = [s.strip() for s in args.source.split(",")] if args.source else None
    con = db.connect()
    try:
        written = load.rebuild(con, RawArchive(), sources=sources)
    finally:
        con.close()

    if not written:
        print("nothing to load")
        return 0
    for source, rows in sorted(written.items()):
        print("{}: {} rows".format(source, rows))
    return 0


def _cmd_coverage(args) -> int:
    from . import config
    from .warehouse import db

    # A read-only connect fails outright if the file is absent, so check first
    # rather than letting duckdb raise at the user.
    if not config.DB_PATH.is_file():
        print("no warehouse yet -- run `ptb ingest` then `ptb rebuild`")
        return 1

    con = db.connect(read_only=True)
    try:
        rows = con.execute(
            "SELECT 'footballdata' AS source, competition, season, count(*) AS matches "
            "  FROM src_footballdata_match GROUP BY 1, 2, 3 "
            "UNION ALL "
            "SELECT 'understat', competition, season, count(*) "
            "  FROM src_understat_match GROUP BY 1, 2, 3 "
            "UNION ALL "
            "SELECT 'asa', "
            "  CASE league WHEN 'nwsl' THEN 'NWSL' WHEN 'mls' THEN 'MLS' "
            "              WHEN 'uslc' THEN 'USLC' WHEN 'usl1' THEN 'USL1' ELSE league END, "
            "  season, count(*) "
            "  FROM src_asa_game GROUP BY 1, 2, 3 "
            "UNION ALL "
            "SELECT 'fpl', 'E0', season, count(*) "
            "  FROM src_fpl_fixture GROUP BY 1, 2, 3 "
            "UNION ALL "
            "SELECT 'draftkings', 'E0', season, count(DISTINCT dk_event_id) "
            "  FROM src_draftkings_odds WHERE season IS NOT NULL GROUP BY 1, 2, 3 "
            "ORDER BY 1, 2, 3"
        ).fetchall()
        unresolved = con.execute("SELECT count(*) FROM unresolved_match").fetchone()[0]
    finally:
        con.close()

    if not rows:
        print("warehouse is empty -- run `ptb ingest` then `ptb rebuild`")
        return 1

    print("{:<14} {:<6} {:<9} {:>8}".format("SOURCE", "COMP", "SEASON", "MATCHES"))
    for source, competition, season, count in rows:
        print("{:<14} {:<6} {:<9} {:>8}".format(source, competition, season, count))
    if unresolved:
        print("\n{} unresolved match(es) -- see the unresolved_match table".format(unresolved))
    return 0


_HANDLERS = {"ingest": _cmd_ingest, "rebuild": _cmd_rebuild, "coverage": _cmd_coverage}


if __name__ == "__main__":
    sys.exit(main())
