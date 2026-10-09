import unittest
from datetime import date

from calendardates import format_date, inclusive_dates, parse_date


class CalendarDateTests(unittest.TestCase):
    def test_format_existing(self):
        self.assertEqual(format_date(date(2024,2,29)),"2024-02-29")

    def test_parse(self):
        self.assertEqual(parse_date("2024-02-29"),date(2024,2,29))

    def test_impossible_date(self):
        with self.assertRaises(ValueError):
            parse_date("2023-02-29")

    def test_inclusive_range(self):
        self.assertEqual(inclusive_dates("2024-02-28","2024-03-01"),[date(2024,2,28),date(2024,2,29),date(2024,3,1)])
