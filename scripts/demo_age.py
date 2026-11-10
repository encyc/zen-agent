#!/usr/bin/env python3
"""Print how many whole days ago a given ISO date was (or the date N days ago)."""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Print today's date minus the given ISO date in whole days, "
            "or the ISO date N days ago with --days."
        ),
    )
    parser.add_argument(
        "iso_date",
        metavar="YYYY-MM-DD",
        nargs="?",
        help="a date in ISO format, e.g. 2024-01-31",
    )
    parser.add_argument(
        "--days",
        metavar="N",
        type=int,
        help="instead of ISO_DATE, print the date N whole days ago",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.days is not None:
        if args.iso_date is not None:
            parser.error("give either ISO_DATE or --days, not both")
        if args.days < 0:
            parser.error(f"--days must be non-negative, got {args.days}")
        print((date.today() - timedelta(days=args.days)).isoformat())
        return 0

    if args.iso_date is None:
        parser.error("give an ISO_DATE (YYYY-MM-DD) or --days N")

    try:
        then = date.fromisoformat(args.iso_date)
    except ValueError:
        parser.error(f"invalid date: {args.iso_date!r} (expected YYYY-MM-DD)")
    print((date.today() - then).days)
    return 0


if __name__ == "__main__":
    sys.exit(main())
