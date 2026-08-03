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
    parser.add_subparsers(dest="command", metavar="<command>")
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


_HANDLERS: dict = {}


if __name__ == "__main__":
    sys.exit(main())
