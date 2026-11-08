import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

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
        self.assertEqual([("2025-W10 ops", 5.5)], weekly_project_totals(self.entries, project="ops"))

    def test_cli_preserves_default_and_filters_project_rows(self):
        cases = [
            ([], [["2025-W10", "7.50h"], ["2025-W11", "8.00h"], ["total", "15.50h"]]),
            (
                ["--by-project"],
                [
                    ["2025-W10", "core", "2.00h"],
                    ["2025-W10", "ops", "5.50h"],
                    ["2025-W11", "core", "8.00h"],
                    ["total", "15.50h"],
                ],
            ),
            (["--by-project", "--project", "ops"], [["2025-W10", "ops", "5.50h"], ["total", "5.50h"]]),
        ]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "export.csv"
            path.write_text(
                "date,hours,project\n2025-03-03,4,ops\n2025-03-04,2,core\n2025-03-10,8,core\n2025-03-05,1.5,ops\n"
            )
            for flags, expected in cases:
                with self.subTest(flags=flags):
                    result = subprocess.run(
                        [sys.executable, "-m", "timesheet", "report", str(path), *flags],
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    self.assertEqual((0, ""), (result.returncode, result.stderr))
                    self.assertEqual(expected, [line.split() for line in result.stdout.splitlines()])

    def test_render_uses_the_usual_columns(self):
        self.assertEqual("2025-W10 ops     5.50h\ntotal            5.50h\n", render([("2025-W10 ops", 5.5)]))


if __name__ == "__main__":
    unittest.main()
