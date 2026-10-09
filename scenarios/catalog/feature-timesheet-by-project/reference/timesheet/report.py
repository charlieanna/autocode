"""Weekly totals report."""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from .entries import Entry
from .weeks import week_key, week_label


def weekly_totals(entries: Iterable[Entry], project: str | None = None) -> list[tuple[str, float]]:
    """Total hours per week, oldest week first."""
    totals: dict[tuple[int, int], float] = defaultdict(float)
    for entry in entries:
        if project is None or entry.project == project:
            totals[week_key(entry.day)] += entry.hours
    return [(week_label(key), round(totals[key], 2)) for key in sorted(totals)]


def weekly_project_totals(entries: Iterable[Entry], project: str | None = None) -> list[tuple[str, float]]:
    """Total hours per ISO week and project, by week and then project name."""
    totals: dict[tuple[tuple[int, int], str], float] = defaultdict(float)
    for entry in entries:
        if project is None or entry.project == project:
            totals[(week_key(entry.day), entry.project)] += entry.hours
    return [(f"{week_label(week)} {name}", round(totals[week, name], 2)) for week, name in sorted(totals)]


def render(rows: list[tuple[str, float]]) -> str:
    if not rows:
        return "no entries\n"
    width = max(len("total"), *(len(label) for label, _ in rows))
    lines = [f"{label:<{width}}  {hours:>7.2f}h" for label, hours in rows]
    lines.append(f"{'total':<{width}}  {sum(hours for _, hours in rows):>7.2f}h")
    return "\n".join(lines) + "\n"
