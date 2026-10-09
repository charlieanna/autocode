import csv
import io
import re
import sqlite3


def _connect(path):
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE IF NOT EXISTS stock(sku TEXT PRIMARY KEY, qty INTEGER NOT NULL)")
    return db


def list_stock(path):
    db = _connect(path)
    try:
        return [{"sku": sku, "qty": qty} for sku, qty in db.execute("SELECT sku, qty FROM stock ORDER BY sku")]
    finally:
        db.close()


def import_stock(path, csv_text):
    db = _connect(path)
    try:
        reader = csv.reader(io.StringIO(csv_text, newline=""), strict=True)
        if next(reader, None) != ["sku", "qty"]:
            raise ValueError("expected sku,qty header")
        count, seen = 0, set()
        with db:
            for row in reader:
                if len(row) != 2:
                    raise ValueError("expected two columns")
                sku, quantity = (value.strip() for value in row)
                if not sku or not re.fullmatch(r"[0-9]+", quantity) or sku in seen:
                    raise ValueError("invalid inventory row")
                seen.add(sku)
                db.execute("INSERT INTO stock VALUES (?, ?) ON CONFLICT(sku) DO UPDATE SET qty=excluded.qty", (sku, int(quantity)))
                db.commit()
                count += 1
        return count
    except (csv.Error, sqlite3.Error, OverflowError) as error:
        db.rollback()
        raise ValueError(str(error)) from error
    finally:
        db.close()
