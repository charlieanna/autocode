def export(records):
    fields = ("name", "email", "note")
    lines = [",".join(fields)]
    for record in records:
        lines.append(",".join("" if record.get(field) is None else record[field] for field in fields))
    return "\r\n".join(lines) + "\r\n"
