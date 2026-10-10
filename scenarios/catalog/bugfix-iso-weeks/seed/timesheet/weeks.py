"""Week helpers.

Weeks start on Monday and are labelled like ``2025-W07``.
"""

from __future__ import annotations

from datetime import date


def week_key(day: date) -> tuple[int, int]:
    """Return the (year, week) that contains ``day``."""
    return day.year, int(day.strftime("%W")) + 1


def week_label(key: tuple[int, int]) -> str:
    year, week = key
    return f"{year}-W{week:02d}"
