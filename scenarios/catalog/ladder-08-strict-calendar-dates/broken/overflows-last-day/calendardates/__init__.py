import re
from datetime import date, timedelta


def format_date(value):
    return value.isoformat()


def parse_date(text):
    if not isinstance(text, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", text):
        raise ValueError("expected YYYY-MM-DD")
    return date(int(text[:4]), int(text[5:7]), int(text[8:]))


def inclusive_dates(start, end):
    first, last = parse_date(start), parse_date(end)
    if first > last:
        raise ValueError("range is reversed")
    result = []
    current = first
    while current <= last:
        result.append(current)
        current += timedelta(days=1)
    return result
