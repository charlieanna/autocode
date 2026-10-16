"""Print a farewell for one name."""

import sys

USAGE = "usage: farewell.py [--formal] NAME"


def farewell(name, formal=False):
    return f"Good evening, {name}." if formal else f"Goodbye, {name}"


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    formal = "--formal" in args
    if formal:
        args.remove("--formal")
    if len(args) != 1 or not args[0].strip():
        print(USAGE, file=sys.stderr)
        return 2
    print(farewell(args[0], formal=formal))
    return 0


if __name__ == "__main__":
    sys.exit(main())
