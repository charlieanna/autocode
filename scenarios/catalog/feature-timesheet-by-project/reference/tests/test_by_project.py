import unittest
from datetime import date

from timesheet.entries import Entry
from timesheet.report import render, weekly_project_totals


class ByProjectTests(unittest.TestCase):
    entries = [
        Entry(date(2025, 3, 3), 4, "ops"),
        Entry(date(2025, 3, 4), 2, "core"),
        Entry(date(2025, 3, 10), 8, "core"),
        Entry(date(2025, 3, 5), 1.5, "ops"),
    ]

    def test_rows_per_week_and_project(self):
        self.assertEqual(
            [("2025-W10 core", 2.0), ("2025-W10 ops", 5.5), ("2025-W11 core", 8.0)], weekly_project_totals(self.entries)
        )

    def test_combines_with_project_filter(self):
        self.assertEqual([("2025-W10 ops", 5.5)], weekly_project_totals(self.entries, "ops"))

    def test_render_uses_the_usual_columns(self):
        self.assertEqual("2025-W10 ops     5.50h\ntotal            5.50h\n", render([("2025-W10 ops", 5.5)]))


if __name__ == "__main__":
    unittest.main()
