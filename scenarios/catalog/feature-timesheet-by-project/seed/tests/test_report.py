import unittest
from datetime import date

from timesheet.entries import Entry
from timesheet.report import render, weekly_totals


def entry(day, hours, project="core"):
    return Entry(date.fromisoformat(day), hours, project)


class WeeklyTotalsTests(unittest.TestCase):
    def test_groups_days_of_the_same_week(self):
        rows = weekly_totals([entry("2025-03-03", 4), entry("2025-03-09", 3.5), entry("2025-03-10", 8)])
        self.assertEqual([("2025-W10", 7.5), ("2025-W11", 8.0)], rows)

    def test_orders_weeks_oldest_first(self):
        rows = weekly_totals([entry("2025-06-16", 1), entry("2025-02-03", 2)])
        self.assertEqual(["2025-W06", "2025-W25"], [label for label, _ in rows])

    def test_filters_by_project(self):
        entries = [entry("2025-05-05", 2, "core"), entry("2025-05-06", 5, "ops")]
        self.assertEqual([("2025-W19", 5.0)], weekly_totals(entries, "ops"))


class RenderTests(unittest.TestCase):
    def test_aligns_rows_and_prints_total(self):
        text = render([("2025-W10", 7.5), ("2025-W11", 12.25)])
        self.assertEqual("2025-W10     7.50h\n2025-W11    12.25h\ntotal       19.75h\n", text)

    def test_empty_report(self):
        self.assertEqual("no entries\n", render([]))


if __name__ == "__main__":
    unittest.main()
