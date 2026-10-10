import unittest
from datetime import date

from timesheet.entries import Entry
from timesheet.report import weekly_totals
from timesheet.weeks import week_key


class IsoWeekTests(unittest.TestCase):
    def test_reported_new_year_week_is_one_row(self):
        entries = [
            Entry(date(2024, 12, 30), 8, "core"),
            Entry(date(2024, 12, 31), 8, "core"),
            Entry(date(2025, 1, 2), 6, "ops"),
            Entry(date(2025, 1, 3), 7.5, "core"),
        ]
        self.assertEqual([("2025-W01", 29.5)], weekly_totals(entries))

    def test_year_boundaries(self):
        cases = {
            date(2021, 1, 1): (2020, 53),
            date(2021, 1, 4): (2021, 1),
            date(2024, 1, 1): (2024, 1),
            date(2026, 12, 31): (2026, 53),
            date(2027, 1, 1): (2026, 53),
            date(2008, 12, 29): (2009, 1),
        }
        for day, expected in cases.items():
            with self.subTest(day=day):
                self.assertEqual(expected, week_key(day))


if __name__ == "__main__":
    unittest.main()
