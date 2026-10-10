"""Load time entries from a CSV export with date, hours and project columns."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path


class EntryError(ValueError):
    """A row in the export could not be parsed."""


@dataclass(frozen=True)
class Entry:
    day: date
    hours: float
    project: str


def parse_row(row: dict[str, str], line: int) -> Entry:
    try:
        day = date.fromisoformat(row["date"].strip())
        hours = float(row["hours"])
    except (KeyError, ValueError) as error:
        raise EntryError(f"line {line}: {error}") from None
    if not 0 <= hours <= 24:
        raise EntryError(f"line {line}: hours must be between 0 and 24, got {hours}")
    return Entry(day, hours, (row.get("project") or "").strip() or "unassigned")


def load(path: Path) -> list[Entry]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return [parse_row(row, number) for number, row in enumerate(reader, start=2)]
