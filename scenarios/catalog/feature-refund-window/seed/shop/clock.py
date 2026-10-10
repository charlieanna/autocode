"""Store time helpers."""

from datetime import UTC, date, datetime

STORE_UTC_OFFSET_HOURS = -8


def store_date(timestamp: int) -> date:
    """The store's calendar date for a Unix timestamp."""
    return datetime.fromtimestamp(timestamp, tz=UTC).date()
