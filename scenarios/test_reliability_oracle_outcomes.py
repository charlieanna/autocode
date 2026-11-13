"""Required reliability cases must observe command completion, not just plausible output."""

import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import catalog, oracle
from harness.project import materialize

ABNORMAL = ("timeout", "signal", "missing", "permission")
NORMAL_FAILURES = (1, 126, 127)


@contextmanager
def command_outcome(matches, outcome):
    """Change only selected product calls; keep their real output and surrounding commands."""
    real_run = subprocess.run
    hits = []

    def run(command, **kwargs):
        if not matches(command, Path(kwargs["cwd"])):
            return real_run(command, **kwargs)
        hits.append(command)
        if outcome == "missing":
            raise FileNotFoundError("fixture could not launch interpreter")
        if outcome == "permission":
            raise PermissionError("fixture could not execute interpreter")
        result = real_run(command, **kwargs)
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(command, kwargs["timeout"], result.stdout, result.stderr)
        return subprocess.CompletedProcess(
            command, -15 if outcome == "signal" else outcome, result.stdout, result.stderr
        )

    with patch.object(oracle.subprocess, "run", side_effect=run):
        yield hits


class OracleProcessOutcomeTests(unittest.TestCase):
    def test_completion_observation_preserves_codes_and_distinguishes_launch_errors(self):
        with tempfile.TemporaryDirectory() as root:
            for outcome in (*ABNORMAL, 0, *NORMAL_FAILURES):
                code = outcome if isinstance(outcome, int) else 0
                command = [sys.executable, "-c", f"print('captured output'); raise SystemExit({code})"]
                with (
                    self.subTest(outcome=outcome),
                    command_outcome(lambda cmd, cwd: cmd == command and outcome in ABNORMAL, outcome),
                ):
                    result = oracle.run(command, Path(root), timeout=30)
                    code = {"timeout": -1, "signal": -15, "missing": 127, "permission": 126}.get(outcome, outcome)
                    self.assertEqual(code, result.returncode)
                    self.assertEqual(command, result.args)
                    self.assertEqual(outcome not in ABNORMAL, result.observed_normal_exit)
                    self.assertEqual(outcome in NORMAL_FAILURES, oracle.normal_failure(result))
                    self.assertEqual("" if outcome in ("missing", "permission") else "captured output\n", result.stdout)
            # An unobserved result cannot silently supply evidence of normal termination.
            self.assertFalse(oracle.normal_failure(subprocess.CompletedProcess(command, 1, "", "")))


class TodoOracleOutcomeTests(unittest.TestCase):
    def setUp(self):
        self.scenario = catalog.load("greenfield-todo-cli")
        self.check = self.scenario.oracle()
        self.project = self.scenario.reference

    def test_error_checks_require_normal_positive_exits(self):
        names = {"unknown_id_fails_without_writing", *(f"malformed_store_{cmd}" for cmd in ("list", "add", "complete"))}

        def errors(command, cwd):
            if command[1:2] != ["todo.py"]:
                return False
            store = cwd / "todos.json"
            return command[2:] == ["complete", "9999"] or (store.exists() and store.read_text() == "{not valid json!!!")

        for outcome in (*ABNORMAL, *NORMAL_FAILURES):
            with self.subTest(outcome=outcome), command_outcome(errors, outcome) as hits:
                checks = self.check(self.project, self.scenario)
                self.assertEqual(4, len(hits))
                self.assertEqual(names if outcome in ABNORMAL else set(), {row.name for row in checks if not row.ok})

    def test_successful_lists_require_zero_even_with_valid_partial_stdout(self):
        for index, expected in (
            (2, "complete_marks_done"),
            (3, "ids_stable_across_restarts"),
            (4, "ids_stable_across_restarts"),
        ):
            for outcome in (*ABNORMAL, *NORMAL_FAILURES):
                count = 0

                def selected_list(command, cwd):
                    nonlocal count
                    if command[1:] == ["todo.py", "list"]:
                        count += 1
                        return count == index
                    return False

                with self.subTest(index=index, outcome=outcome), command_outcome(selected_list, outcome) as hits:
                    checks = self.check(self.project, self.scenario)
                    self.assertEqual(1, len(hits))
                    self.assertEqual({expected}, {row.name for row in checks if not row.ok})

    def test_stable_id_setup_adds_require_zero_after_writing_the_store(self):
        for text in ("first", "second"):
            for outcome in ("timeout", "signal", *NORMAL_FAILURES):
                with (
                    self.subTest(text=text, outcome=outcome),
                    command_outcome(lambda cmd, cwd: cmd[1:] == ["todo.py", "add", text], outcome) as hits,
                ):
                    checks = self.check(self.project, self.scenario)
                    self.assertEqual(1, len(hits))
                    self.assertEqual({"ids_stable_across_restarts"}, {row.name for row in checks if not row.ok})


class PythonChangeOracleOutcomeTests(unittest.TestCase):
    def test_timesheet_and_iso_original_code_checks_require_normal_failure(self):
        for name in ("feature-timesheet-by-project", "bugfix-iso-weeks"):
            scenario = catalog.load(name)
            with tempfile.TemporaryDirectory() as root:
                project = materialize(scenario.seed, Path(root) / "project", scenario.reference)
                for outcome in (*ABNORMAL, 0, *NORMAL_FAILURES):
                    count = 0

                    def original_suite(command, cwd):
                        nonlocal count
                        if command[1:] == ["-m", "unittest", "discover", "-s", "tests", "-t", "."]:
                            count += 1
                            return count == 2
                        return False

                    with self.subTest(scenario=name, outcome=outcome), command_outcome(original_suite, outcome) as hits:
                        checks = scenario.oracle()(project, scenario)
                        self.assertEqual(1, len(hits))
                        self.assert_original_code_detail(checks, outcome)
                        self.assertEqual(
                            {"new_tests_fail_on_original_code"} if outcome in (*ABNORMAL, 0) else set(),
                            {row.name for row in checks if not row.ok},
                        )

    def assert_original_code_detail(self, checks, outcome):
        detail = next(row.detail for row in checks if row.name == "new_tests_fail_on_original_code")
        expected = (
            "original-code check did not complete normally"
            if outcome in ABNORMAL
            else "delivered tests still pass"
            if outcome == 0
            else "delivered tests fail"
        )
        self.assertIn(expected, detail)
        if outcome in ABNORMAL:
            code = {"timeout": -1, "signal": -15, "missing": 127, "permission": 126}[outcome]
            self.assertIn(f"exit {code}:", detail)

    def test_docutils_original_code_check_requires_normal_failure_without_seed_download(self):
        check = catalog.load("bugfix-realrepo-docutils-number-lines").oracle()
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            seed, overlay, hidden = root / "seed", root / "overlay", root / "hidden"
            files = {
                "docutils/docutils/__init__.py": "",
                "docutils/docutils/value.py": "VALUE = 0\n",
                "docutils/test/__init__.py": "",
                "docutils/test/alltests.py": (
                    "import sys, unittest\nfrom pathlib import Path\n"
                    "sys.path.insert(0, str(Path(__file__).resolve().parents[1]))\n"
                    "unittest.main(module='test.test_fix')\n"
                ),
            }
            for name, text in files.items():
                path = seed / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
            test = (
                "import unittest\nfrom docutils.value import VALUE\n"
                "class ValueTests(unittest.TestCase):\n"
                "    def test_changed_value(self):\n        self.assertEqual(1, VALUE)\n"
            )
            for name, text in {"docutils/docutils/value.py": "VALUE = 1\n", "docutils/test/test_fix.py": test}.items():
                path = overlay / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
            hidden.mkdir()
            (hidden / "test_hidden.py").write_text(test)
            scenario = SimpleNamespace(seed=seed, dir=root)
            project = materialize(seed, root / "project", overlay)
            for outcome in (*ABNORMAL, 0, *NORMAL_FAILURES):
                with (
                    self.subTest(outcome=outcome),
                    command_outcome(lambda cmd, cwd: cmd[1:] == ["-m", "unittest", "test.test_fix"], outcome) as hits,
                ):
                    checks = check(project, scenario)
                    self.assertEqual(1, len(hits))
                    self.assert_original_code_detail(checks, outcome)
                    self.assertEqual(
                        {"new_tests_fail_on_original_code"} if outcome in (*ABNORMAL, 0) else set(),
                        {row.name for row in checks if not row.ok},
                    )


if __name__ == "__main__":
    unittest.main()
