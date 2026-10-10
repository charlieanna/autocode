import unittest
from datetime import date

from calendardates import format_date


class CalendarDateTests(unittest.TestCase):
    def test_format_existing(self):
        self.assertEqual(format_date(date(2024, 2, 29)), "2024-02-29")
