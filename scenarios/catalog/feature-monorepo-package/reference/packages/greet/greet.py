"""Print a greeting for one name."""

import sys

USAGE = "usage: greet.py [--formal] NAME"


def greeting(name, formal=False):
    return f"Good day, {name}." if formal else f"Hello, {name}"


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    formal = "--formal" in args
    if formal:
        args.remove("--formal")
    if len(args) != 1 or not args[0].strip():
        print(USAGE, file=sys.stderr)
        return 2
    print(greeting(args[0], formal=formal))
    return 0


if __name__ == "__main__":
    sys.exit(main())
