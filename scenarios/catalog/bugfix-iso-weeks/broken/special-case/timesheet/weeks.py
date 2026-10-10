"""Week helpers.

Weeks start on Monday and are labelled like ``2025-W07``.
"""

from __future__ import annotations

from datetime import date


def week_key(day: date) -> tuple[int, int]:
    """Return the (year, week) that contains ``day``."""
    week = int(day.strftime("%W")) + 1
    if week > 52:
        # Late December days belong to the first week of the next year.
        return day.year + 1, 1
    return day.year, week


def week_label(key: tuple[int, int]) -> str:
    year, week = key
    return f"{year}-W{week:02d}"
