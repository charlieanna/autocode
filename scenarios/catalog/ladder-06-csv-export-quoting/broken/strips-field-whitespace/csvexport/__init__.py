import csv
import io


def export(records):
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    fields = ("name", "email", "note")
    writer.writerow(fields)
    for record in records:
        writer.writerow(["" if record.get(field) is None else record[field].strip() for field in fields])
    return output.getvalue()
