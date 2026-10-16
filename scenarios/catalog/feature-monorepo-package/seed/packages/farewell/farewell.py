"""Print a farewell for one name."""

import sys

USAGE = "usage: farewell.py NAME"


def farewell(name):
    return f"Goodbye, {name}"


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or not args[0].strip():
        print(USAGE, file=sys.stderr)
        return 2
    print(farewell(args[0]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
