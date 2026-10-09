import tempfile
import unittest
from datetime import date
from pathlib import Path

from timesheet.entries import EntryError, load, parse_row


class ParseRowTests(unittest.TestCase):
    def test_parses_a_valid_row(self):
        entry = parse_row({"date": "2025-04-01", "hours": "7.5", "project": " core "}, 2)
        self.assertEqual((date(2025, 4, 1), 7.5, "core"), (entry.day, entry.hours, entry.project))

    def test_missing_project_is_unassigned(self):
        self.assertEqual("unassigned", parse_row({"date": "2025-04-01", "hours": "1"}, 2).project)

    def test_rejects_impossible_hours(self):
        with self.assertRaisesRegex(EntryError, "line 7"):
            parse_row({"date": "2025-04-01", "hours": "25"}, 7)

    def test_rejects_bad_dates(self):
        with self.assertRaises(EntryError):
            parse_row({"date": "2025-02-30", "hours": "1"}, 2)


class LoadTests(unittest.TestCase):
    def test_reports_the_csv_line_number(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "export.csv"
            path.write_text("date,hours,project\n2025-04-01,2,core\n2025-04-02,x,core\n")
            with self.assertRaisesRegex(EntryError, "line 3"):
                load(path)


if __name__ == "__main__":
    unittest.main()
