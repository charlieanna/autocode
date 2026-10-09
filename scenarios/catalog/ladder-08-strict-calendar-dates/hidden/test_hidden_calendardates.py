import unittest
from datetime import date, timedelta

from calendardates import format_date, inclusive_dates, parse_date


class HiddenCalendarDateTests(unittest.TestCase):
    def test_strict_syntax(self):
        for text in ["20240229","2024-W09-4","2024-2-29","2024-02-9"," 2024-02-29","2024-02-29\n","２０２４-０２-２９","0000-01-01","10000-01-01","2023-02-29","1900-02-29","2024-04-31","2024-00-01","2024-01-00",None,20240229,b"2024-02-29"]:
            with self.subTest(text=text),self.assertRaises(ValueError):
                parse_date(text)

    def test_calendar_round_trip(self):
        for year in [1,4,99,100,400,1600,1900,1999,2000,2024,2100,2400,9999]:
            for month in range(1,13):
                first=date(year,month,1)
                for offset in range(28):
                    current=first+timedelta(days=offset)
                    self.assertEqual(parse_date(current.isoformat()),current)
                    self.assertEqual(format_date(current),current.isoformat())
        self.assertEqual(parse_date("2000-02-29"),date(2000,2,29))

    def test_ranges_at_boundaries(self):
        for start,end in [("0001-01-01","0001-01-01"),("9999-12-31","9999-12-31"),("1999-12-30","2000-01-02"),("1900-02-28","1900-03-01"),("2000-02-28","2000-03-01")]:
            a,b=date.fromisoformat(start),date.fromisoformat(end)
            expected=[date.fromordinal(n) for n in range(a.toordinal(),b.toordinal()+1)]
            self.assertEqual(inclusive_dates(start,end),expected)
        for start,end in [("2024-01-02","2024-01-01"),("20240101","2024-01-02"),("2024-01-01","2024-02-30")]:
            with self.assertRaises(ValueError): inclusive_dates(start,end)
