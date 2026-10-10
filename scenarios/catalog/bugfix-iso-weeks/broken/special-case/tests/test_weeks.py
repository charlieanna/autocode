import unittest
from datetime import date

from timesheet.entries import Entry
from timesheet.report import weekly_totals


class NewYearTests(unittest.TestCase):
    def test_reported_new_year_week_is_one_row(self):
        entries = [
            Entry(date(2024, 12, 30), 8, "core"),
            Entry(date(2024, 12, 31), 8, "core"),
            Entry(date(2025, 1, 2), 6, "ops"),
            Entry(date(2025, 1, 3), 7.5, "core"),
        ]
        self.assertEqual([("2025-W01", 29.5)], weekly_totals(entries))


if __name__ == "__main__":
    unittest.main()
