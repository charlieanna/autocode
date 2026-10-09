"""Hidden acceptance tests. They exercise only the documented command line,
so a fix may restructure the internals freely. Run from the project root."""
import subprocess
import sys
import tempfile
import unittest
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path


def report(csv_text, *extra):
    with tempfile.TemporaryDirectory() as tmp:
        export = Path(tmp) / "export.csv"
        export.write_text(csv_text)
        return subprocess.run([sys.executable, "-m", "timesheet", "report", str(export), *extra],
                              capture_output=True, text=True, timeout=60)


def expected_output(rows):
    """The documented format: label, two spaces, hours right-aligned in 7 columns."""
    if not rows:
        return "no entries\n"
    width = max(len("total"), *(len(label) for label, _ in rows))
    lines = [f"{label:<{width}}  {hours:>7.2f}h" for label, hours in rows]
    lines.append(f"{'total':<{width}}  {sum(hours for _, hours in rows):>7.2f}h")
    return "\n".join(lines) + "\n"


class HiddenCliTests(unittest.TestCase):
    def test_reported_example(self):
        result = report("date,hours,project\n2024-12-30,8,core\n2024-12-31,8,core\n"
                        "2025-01-02,6,ops\n2025-01-03,7.5,core\n")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("2025-W01    29.50h\ntotal       29.50h\n", result.stdout)

    def test_every_day_from_2000_to_2030(self):
        rows, totals = ["date,hours,project"], defaultdict(float)
        day = date(2000, 1, 1)
        while day <= date(2030, 12, 31):
            rows.append(f"{day.isoformat()},1,core")
            iso = day.isocalendar()
            totals[(iso.year, iso.week)] += 1
            day += timedelta(days=1)
        result = report("\n".join(rows) + "\n")
        self.assertEqual(0, result.returncode, result.stderr)
        expected = [(f"{year}-W{week:02d}", hours) for (year, week), hours in sorted(totals.items())]
        self.assertEqual(expected_output(expected), result.stdout)

    def test_week_53_years_are_kept(self):
        result = report("date,hours\n2020-12-28,2\n2021-01-03,3\n2026-12-31,4\n2027-01-01,5\n")
        self.assertEqual(expected_output([("2020-W53", 5.0), ("2026-W53", 9.0)]), result.stdout)

    def test_project_filter_is_unchanged(self):
        result = report("date,hours,project\n2025-05-05,2,core\n2025-05-06,5,ops\n", "--project", "ops")
        self.assertEqual(expected_output([("2025-W19", 5.0)]), result.stdout)

    def test_malformed_row_still_exits_1_with_line_number(self):
        result = report("date,hours\n2025-05-05,2\n2025-05-06,lots\n")
        self.assertEqual(1, result.returncode)
        self.assertIn("line 3", result.stderr)
        self.assertEqual("", result.stdout)


if __name__ == "__main__":
    unittest.main()
