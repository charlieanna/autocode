"""Week helpers.

Weeks follow ISO 8601: they start on Monday, week 1 contains the year's first
Thursday, and labels use the ISO week-numbering year, e.g. ``2025-W07``.
"""

from __future__ import annotations

from datetime import date


def week_key(day: date) -> tuple[int, int]:
    """Return the ISO (year, week) that contains ``day``."""
    iso = day.isocalendar()
    return iso.year, iso.week


def week_label(key: tuple[int, int]) -> str:
    year, week = key
    return f"{year}-W{week:02d}"
