import csv
import io


def export(records):
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    fields = ("name", "email", "note")
    writer.writerow(fields)
    for record in records:
        writer.writerow([str(record.get(field, "")) for field in fields])
    return output.getvalue()
