import argparse
import json
import re
import sqlite3
import sys


def _integer(text):
    text = text.strip()
    if not re.fullmatch(r"[+-]?\d(?:_?\d)*", text):
        raise ValueError("invalid integer")
    negative = text.startswith("-")
    digits = text.lstrip("+-").replace("_", "")
    value = 0
    for offset in range(0, len(digits), 9):
        part = digits[offset:offset + 9]
        value = value * 10 ** len(part) + int(part)
    return -value if negative else value


def _decimal(value):
    negative = value < 0
    value = abs(value)
    chunks = []
    while value:
        value, remainder = divmod(value, 1_000_000_000)
        chunks.append(remainder)
    if not chunks:
        return "0"
    result = str(chunks.pop()) + "".join(f"{part:09d}" for part in reversed(chunks))
    return "-" + result if negative else result


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("add", "adjust", "delete"):
        command = commands.add_parser(name)
        command.add_argument("sku")
        if name != "delete":
            command.add_argument("amount", type=_integer)
    commands.add_parser("list")
    args = parser.parse_args(argv)
    try:
        with sqlite3.connect(args.db) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("CREATE TABLE IF NOT EXISTS inventory (sku TEXT PRIMARY KEY, quantity TEXT NOT NULL)")
            if args.command == "list":
                records = ["{\"sku\": " + json.dumps(sku, ensure_ascii=False) + ", \"quantity\": " + quantity + "}"
                           for sku, quantity in db.execute("SELECT sku, quantity FROM inventory ORDER BY sku")]
                print("[" + ", ".join(records) + "]")
            else:
                if not args.sku:
                    raise ValueError("SKU is required")
                if args.command == "add":
                    if args.amount < 0:
                        raise ValueError("negative stock")
                    db.execute("INSERT OR REPLACE INTO inventory VALUES (?, ?)", (args.sku, _decimal(args.amount)))
                elif args.command == "adjust":
                    row = db.execute("SELECT quantity FROM inventory WHERE sku=?", (args.sku,)).fetchone()
                    if row is None:
                        raise ValueError("unknown SKU")
                    quantity = _integer(row[0]) + args.amount
                    if quantity < 0:
                        raise ValueError("negative stock")
                    db.execute("UPDATE inventory SET quantity=? WHERE sku=?", (_decimal(quantity), args.sku))
                elif db.execute("DELETE FROM inventory WHERE sku=?", (args.sku,)).rowcount != 1:
                    raise ValueError("unknown SKU")
        return 0
    except (sqlite3.Error, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2
