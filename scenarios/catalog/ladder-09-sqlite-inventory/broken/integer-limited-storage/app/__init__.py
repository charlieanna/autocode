import argparse
import json
import sqlite3
import sys


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("add", "adjust", "delete"):
        command = commands.add_parser(name)
        command.add_argument("sku")
        if name != "delete":
            command.add_argument("amount", type=int)
    commands.add_parser("list")
    args = parser.parse_args(argv)
    try:
        with sqlite3.connect(args.db) as db:
            db.execute("CREATE TABLE IF NOT EXISTS inventory (sku TEXT PRIMARY KEY, quantity INTEGER NOT NULL CHECK(quantity >= 0))")
            if args.command == "list":
                print(json.dumps([{"sku": row[0], "quantity": row[1]} for row in db.execute("SELECT sku, quantity FROM inventory ORDER BY sku")], ensure_ascii=False))
            else:
                if not args.sku:
                    raise ValueError("SKU is required")
                if args.command == "add":
                    db.execute("INSERT INTO inventory VALUES (?, ?)", (args.sku, args.amount))
                elif args.command == "adjust":
                    cursor = db.execute("UPDATE inventory SET quantity=quantity+? WHERE sku=?", (args.amount, args.sku))
                    if cursor.rowcount != 1:
                        raise ValueError("unknown SKU")
                elif db.execute("DELETE FROM inventory WHERE sku=?", (args.sku,)).rowcount != 1:
                    raise ValueError("unknown SKU")
        return 0
    except (sqlite3.Error, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2
