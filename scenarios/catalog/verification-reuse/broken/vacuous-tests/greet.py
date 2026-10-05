import sys

if len(sys.argv) != 2:
    print("usage: greet.py NAME", file=sys.stderr)
    raise SystemExit(2)
print("Hello, " + sys.argv[1])
