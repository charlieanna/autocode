"""The advisory base-revision check for planned exact-output examples (#676)."""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from autocode_base_examples import check, differences, notes, planned


def criterion(command, expected):
    return (f"Given the base data, when `{command}` runs, then it exits 0 and "
            f"writes exactly `{expected}` to stdout.")


class PlannedExamplesTests(unittest.TestCase):
    def test_only_criteria_with_a_command_and_exact_stdout_are_planned(self):
        body = {"acceptance_criteria": [
            {"id": "AC1", "criterion": criterion("python3 report.py", "total  1\\n")},
            {"id": "AC2", "criterion": "Print a header row."},
            {"id": "AC3", "criterion": "Given x, when `tool` runs, then it is fine."}]}
        rows = planned(body)
        self.assertEqual(["AC1"], [row["id"] for row in rows])
        self.assertEqual("total  1\n", rows[0]["expected"])

    def test_differences_show_only_the_changed_lines(self):
        changed = differences("a 12\nb 2\n", "a 10\nb 2\n")
        self.assertEqual(["-a 10", "+a 12"], changed)


class BaseRevisionCheckTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="base-check-test-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        (self.root / "report.py").write_text('print("total" + " " * 10 + "15.50h")\n')
        git = ["git", "-C", str(self.root)]
        subprocess.run([*git, "init", "-q"], check=True)
        subprocess.run([*git, "add", "report.py"], check=True)
        subprocess.run([*git, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base"], check=True)

    def state(self, expected_spaces, command=None):
        command = command or f"{sys.executable} report.py"
        body = {"acceptance_criteria": [
            {"id": "AC1", "criterion": criterion(command, "total" + " " * expected_spaces + "15.50h\\n")}]}
        return {"workspace": str(self.root), "goal_contract": {"body": body}}

    def test_a_planned_row_that_differs_from_the_base_is_noted(self):
        state = self.state(expected_spaces=12)
        check(state)
        lines = notes(state)
        self.assertTrue(any("AC1" in line and "base revision" in line for line in lines), lines)
        self.assertTrue(any("total          15.50h" in line or "+total" in line for line in lines), lines)

    def test_a_planned_row_that_matches_the_base_is_not_noted(self):
        state = self.state(expected_spaces=10)
        check(state)
        self.assertEqual([], notes(state))

    def test_a_command_missing_on_the_base_is_a_new_behavior_not_a_note(self):
        state = self.state(expected_spaces=12, command=f"{sys.executable} new_feature.py")
        check(state)
        self.assertEqual([], notes(state))

    def test_the_check_is_not_rerun_for_the_same_examples(self):
        state = self.state(expected_spaces=12)
        first = check(state)
        (self.root / "report.py").write_text('print("changed")\n')
        self.assertIs(first, check(state))

    def test_notes_are_dropped_when_the_planned_examples_change(self):
        state = self.state(expected_spaces=12)
        check(state)
        row = state["goal_contract"]["body"]["acceptance_criteria"][0]
        row["criterion"] = criterion(f"{sys.executable} report.py", "total" + " " * 14 + "15.50h\\n")
        self.assertEqual([], notes(state))
        row["criterion"] = criterion(f"{sys.executable} report.py", "total" + " " * 12 + "15.50h\\n")
        check(state)
        self.assertTrue(notes(state))

    def test_a_workspace_that_is_not_a_checkout_says_so(self):
        plain = Path(tempfile.mkdtemp(prefix="base-check-plain-"))
        self.addCleanup(shutil.rmtree, plain, True)
        state = self.state(expected_spaces=12)
        state["workspace"] = str(plain)
        check(state)
        self.assertTrue(any("not a git checkout" in line for line in notes(state)))


if __name__ == "__main__":
    unittest.main()
