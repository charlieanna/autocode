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
    # type(q) is int: a JSON true is a bool, which isinstance(q, int) would let through as 1.
    if not isinstance(data, dict) or not all(
            isinstance(items, dict) and all(type(q) is int and q > 0 for q in items.values())
            for items in data.values()):
        raise Refused("stock.json is malformed: expected {location: {sku: positive int}}")
    return data


def save(data):
    tmp = STORE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, STORE)


def quantity(text):
    # ASCII digits only: str.isdigit() alone also passes a superscript 2 (int() raises) and an Arabic-Indic 3.
    if not (text.isascii() and text.isdigit()):
        raise Refused(f"quantity must be a positive integer, got {text!r}")
    try:
        value = int(text)
    except ValueError:  # more digits than int() reads (sys.get_int_max_str_digits(), 4300 by default)
        raise Refused(f"quantity is too large: {len(text)} digits") from None
    if value <= 0:
        raise Refused(f"quantity must be a positive integer, got {text!r}")
    return value


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


def take(data, location, sku, qty):
    have = data.get(location, {}).get(sku, 0)
    if have < qty:
        raise Refused(f"only {have} of {sku} at {location}, cannot take {qty}")
    data[location][sku] = have - qty
    if not data[location][sku]:
        del data[location][sku]
    if not data[location]:
        del data[location]


def cmd_move(args):
    qty = quantity(args.qty)
    if args.source == args.target:
        raise Refused("FROM and TO must differ")
    data = load()
    take(data, args.source, args.sku, qty)
    items = data.setdefault(args.target, {})
    items[args.sku] = items.get(args.sku, 0) + qty
    save(data)
    return 0


def cmd_remove(args):
    qty = quantity(args.qty)
    data = load()
    take(data, args.location, args.sku, qty)
    save(data)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="stock.py")
    commands = parser.add_subparsers(dest="command", required=True)
    receive = commands.add_parser("receive", help="add units of a SKU at a location")
    receive.add_argument("sku")
    receive.add_argument("qty")
    receive.add_argument("location")
    receive.set_defaults(run=cmd_receive)
    move = commands.add_parser("move", help="move units of a SKU between locations")
    move.add_argument("sku")
    move.add_argument("qty")
    move.add_argument("source")
    move.add_argument("target")
    move.set_defaults(run=cmd_move)
    remove = commands.add_parser("remove", help="remove units of a SKU at a location")
    remove.add_argument("sku")
    remove.add_argument("qty")
    remove.add_argument("location")
    remove.set_defaults(run=cmd_remove)
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
