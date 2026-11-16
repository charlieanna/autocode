"""The report command: prints the totals of an orders file, one amount per line."""

import sys

import cache
import totals


def load(path):
    amounts = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                amounts = amounts + [float(line)]
    return amounts


def main(argv):
    path = argv[1]
    amounts = cache.get(path, lambda: load(path))
    print(f"total: {totals.total(amounts):.2f}")
    print(f"average: {totals.average(amounts):.2f}")
    print(f"with tax: {totals.total_with_tax(amounts):.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
