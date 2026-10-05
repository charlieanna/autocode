#!/usr/bin/env python3
"""Warehouse stock CLI. README.md has the command summary and the exit-code contract."""
import argparse
import json
import os
import sys
from pathlib import Path

STORE = Path("stock.json")
REFUSED = 2


class Refused(Exception):
    pass


def load():
    if not STORE.exists():
        return {}
    try:
        data = json.loads(STORE.read_text())
    except ValueError as error:
        raise Refused(f"stock.json is malformed: {error}") from None
    if not isinstance(data, dict) or not all(
            isinstance(items, dict) and all(isinstance(q, int) and q > 0 for q in items.values())
            for items in data.values()):
        raise Refused("stock.json is malformed: expected {location: {sku: positive int}}")
    return data


def save(data):
    tmp = STORE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, STORE)


def quantity(text):
    if not text.isdigit() or int(text) <= 0:
        raise Refused(f"quantity must be a positive integer, got {text!r}")
    return int(text)


def cmd_receive(args):
    qty = quantity(args.qty)
    data = load()
    items = data.setdefault(args.location, {})
    items[args.sku] = items.get(args.sku, 0) + qty
    save(data)
    return 0


def cmd_show(args):
    data = load()
    for location in sorted(data):
        for sku, qty in sorted(data[location].items()):
            print(f"{location} {sku} {qty}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="stock.py")
    commands = parser.add_subparsers(dest="command", required=True)
    receive = commands.add_parser("receive", help="add units of a SKU at a location")
    receive.add_argument("sku")
    receive.add_argument("qty")
    receive.add_argument("location")
    receive.set_defaults(run=cmd_receive)
    show = commands.add_parser("show", help="list stock as LOCATION SKU QTY lines")
    show.set_defaults(run=cmd_show)
    args = parser.parse_args(argv)
    try:
        return args.run(args)
    except Refused as error:
        print(f"stock.py: {error}", file=sys.stderr)
        return REFUSED


if __name__ == "__main__":
    sys.exit(main())
