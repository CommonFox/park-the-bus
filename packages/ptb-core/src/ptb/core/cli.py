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

    total = 0
    for name in names:
        keys = get_source(name).ingest(archive, **options)
        print("{}: archived {} payload(s)".format(name, len(keys)))
        total += len(keys)

    return 0 if total else 1


_HANDLERS = {"ingest": _cmd_ingest}


if __name__ == "__main__":
    sys.exit(main())
