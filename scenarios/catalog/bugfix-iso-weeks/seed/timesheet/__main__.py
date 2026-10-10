"""Command line: python3 -m timesheet report EXPORT.csv [--project NAME]"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .entries import EntryError, load
from .report import render, weekly_totals


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="timesheet")
    commands = parser.add_subparsers(dest="command", required=True)
    report = commands.add_parser("report", help="print total hours per week")
    report.add_argument("export", type=Path)
    report.add_argument("--project")
    args = parser.parse_args(argv)
    try:
        entries = load(args.export)
    except (OSError, EntryError) as error:
        print(f"timesheet: {error}", file=sys.stderr)
        return 1
    sys.stdout.write(render(weekly_totals(entries, args.project)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
