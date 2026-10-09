"""Export rows of dictionaries as CSV text."""
import csv
import io


def export(rows, columns):
    """CSV text with a header row. Missing or None cells are written as empty fields."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow(["" if row.get(column) is None else row.get(column) for column in columns])
    return buffer.getvalue()
