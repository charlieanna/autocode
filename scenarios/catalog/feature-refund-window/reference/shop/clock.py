"""Store time helpers."""

from datetime import date, datetime, timedelta, timezone

STORE_UTC_OFFSET_HOURS = -8
STORE_TIME = timezone(timedelta(hours=STORE_UTC_OFFSET_HOURS))


def store_date(timestamp: int) -> date:
    """The store's calendar date for a Unix timestamp."""
    return datetime.fromtimestamp(timestamp, tz=STORE_TIME).date()
