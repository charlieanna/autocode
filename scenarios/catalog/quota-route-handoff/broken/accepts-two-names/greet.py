"""Deterministic greeting CLI."""
import sys

USAGE = "usage: greet.py NAME"


def greet(name: str) -> str:
    return f"Hello, {name}"


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    argv = [" ".join(argv)] if argv else argv
    if len(argv) != 1 or not argv[0]:
        print(USAGE, file=sys.stderr)
        return 2
    print(greet(argv[0]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
