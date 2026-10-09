"""Hidden acceptance tests for --by-project, through the documented command line."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

EXPORT = ("date,hours,project\n2025-03-03,4,ops\n2025-03-04,2,core\n2025-03-05,1.5,ops\n"
          "2025-03-10,8,core\n2024-12-30,3,web\n2025-01-02,1,core\n")


def report(*extra, export=EXPORT):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "export.csv"
        path.write_text(export)
        return subprocess.run([sys.executable, "-m", "timesheet", "report", str(path), *extra],
                              capture_output=True, text=True, timeout=60)


def expected_output(rows):
    """The documented layout: labels left-aligned, two spaces, hours right-aligned in 7 columns."""
    width = max(len("total"), *(len(label) for label, _ in rows))
    lines = [f"{label:<{width}}  {hours:>7.2f}h" for label, hours in rows]
    lines.append(f"{'total':<{width}}  {sum(hours for _, hours in rows):>7.2f}h")
    return "\n".join(lines) + "\n"


class HiddenByProjectTests(unittest.TestCase):
    def test_rows_per_week_and_project(self):
        self.assertEqual(expected_output([("2025-W01 core", 1.0), ("2025-W01 web", 3.0), ("2025-W10 core", 2.0),
                                          ("2025-W10 ops", 5.5), ("2025-W11 core", 8.0)]),
                         report("--by-project").stdout)

    def test_combined_with_project_filter(self):
        self.assertEqual(expected_output([("2025-W10 ops", 5.5)]), report("--by-project", "--project", "ops").stdout)

    def test_default_output_is_unchanged(self):
        self.assertEqual(expected_output([("2025-W01", 4.0), ("2025-W10", 7.5), ("2025-W11", 8.0)]), report().stdout)

    def test_empty_export(self):
        self.assertEqual("no entries\n", report("--by-project", export="date,hours,project\n").stdout)
