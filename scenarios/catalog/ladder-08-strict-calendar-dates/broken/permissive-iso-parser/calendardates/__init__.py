from datetime import date, timedelta


def format_date(value):
    return value.isoformat()


def parse_date(text):
    if not isinstance(text, str):
        raise ValueError("expected string")
    return date.fromisoformat(text)


def inclusive_dates(start, end):
    first, last = parse_date(start), parse_date(end)
    if first > last:
        raise ValueError("range is reversed")
    return [first + timedelta(days=offset) for offset in range((last - first).days + 1)]
