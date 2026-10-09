import argparse
import csv
import json
import re
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description="Validate contact CSV")
    parser.add_argument("path")
    args = parser.parse_args(argv)
    try:
        with open(args.path, encoding="utf-8", newline="") as source:
            reader = csv.reader(source, strict=True)
            header = next((row for row in reader if row), None)
            if header != ["name", "age", "email"]:
                raise ValueError("expected header name,age,email")
            errors = []
            count = 0
            for row in reader:
                if not row:
                    continue
                count += 1
                if len(row) != 3:
                    fields = ["columns"]
                else:
                    name, age, email = (field.strip() for field in row)
                    fields = []
                    if not name:
                        fields.append("name")
                    if not re.fullmatch(r"[0-9]+", age) or not 1 <= int(age) <= 130:
                        fields.append("age")
                    if not re.fullmatch(r"[^@\s]+@[^@\s]+", email):
                        fields.append("email")
                if fields:
                    errors.append({"row": count + 1, "fields": fields})
    except (OSError, UnicodeError, csv.Error, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps({"rows": count, "errors": errors}))
    return 1 if errors else 0
