import argparse
import collections
import json
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description="Count ASCII word tokens from stdin")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit <= 0:
        parser.error("limit must be positive")
    counts = collections.Counter(sys.stdin.read().lower().split())
    rows = [
        {"word": word, "count": count} for word, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]
    print(json.dumps(rows[: args.limit]))
    return 0
