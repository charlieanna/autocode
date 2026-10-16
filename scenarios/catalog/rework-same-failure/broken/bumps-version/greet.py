import sys


def main():
    if len(sys.argv) != 2 or not sys.argv[1].strip():
        print("usage: greet.py NAME", file=sys.stderr)
        return 2
    print("Hello, " + sys.argv[1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
