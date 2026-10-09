import argparse
import csv
import json
import re
import sys


def strict_quoted_lines(source):
    """Keep CSV parsing in csv.reader, rejecting quotes in unquoted fields."""
    state = "start"
    for line in source:
        for char in line:
            if state == "quoted":
                if char == '"':
                    state = "closed"
            elif state == "closed" and char == '"':
                state = "quoted"  # doubled quote inside a quoted field
            elif char in ",\r\n":
                state = "start"
            elif state == "start":
                state = "quoted" if char == '"' else "unquoted"
            elif char == '"' or state == "closed":
                raise csv.Error("malformed CSV quoting")
        yield line
    if state == "quoted":
        raise csv.Error("unterminated quoted field")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Validate contact CSV")
    parser.add_argument("path")
    args = parser.parse_args(argv)
    limit = sys.maxsize
    while True:
        try:
            csv.field_size_limit(limit)
            break
        except OverflowError:
            limit //= 10
    try:
        with open(args.path, encoding="utf-8", newline="") as source:
            reader = csv.reader(strict_quoted_lines(source), strict=True)
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
                    bounded_age = age.lstrip("0") or "0"
                    if (not re.fullmatch(r"[0-9]+", age) or len(bounded_age) > 3
                            or int(bounded_age) > 130):
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
