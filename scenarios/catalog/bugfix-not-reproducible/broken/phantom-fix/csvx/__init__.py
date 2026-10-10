"""Export rows of dictionaries as CSV text."""

import csv
import io


def _cell(value):
    # Defensive: never let None (or its text form) leak into the file.
    if value is None or value == "None":
        return ""
    return value


def export(rows, columns):
    """CSV text with a header row. Missing or None cells are written as empty fields."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow([_cell(row.get(column)) for column in columns])
    return buffer.getvalue()
