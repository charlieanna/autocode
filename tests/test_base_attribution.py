"""Whose failure a planned check's failure is: the base revision's or the change's (#3160)."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_base_attribution as attribution
import autocode_base_command as base_command
import autocode_evidence_document as document
import autocode_evidence_provenance as provenance
import autocode_regression as regression
import autocode_run_view as run_view


class JudgeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def run_in(self, name, text, exit_code=1, **extra):
        """A receipt as the clean replay (candidate) or autocode_base_command.run (base) leaves it."""
        folder = self.root / name
        tree = folder / "scratch" / ("tree" if name == "candidate" else "base")
        tree.mkdir(parents=True)
        output = folder / "scratch-command.log"
        output.write_text(text.replace("{tree}", str(tree)))
        receipt = {
            "command": "make lint",
            "exit_code": exit_code,
            "timed_out": False,
            "error": "",
            "output": str(output),
        }
        return {**receipt, **({"tree": str(tree)} if name == "base" else {}), **extra}

    def test_the_same_output_in_each_copys_own_directory_is_the_bases(self):
        result = attribution.judge(
            self.run_in("candidate", "{tree}/legacy.py:3: E1 old\n"),
            self.run_in("base", "{tree}/legacy.py:3: E1 old\n"),
        )
        self.assertEqual("base", result["kind"])
        self.assertEqual(64, len(result["normalized_output_sha256"]))

    def test_anything_short_of_an_identical_completed_failure_is_the_changes(self):
        for candidate, base, reason in (
            ({"timed_out": True}, {}, "did not complete on the change"),
            ({"error": "cannot run"}, {}, "did not complete on the change"),
            ({}, {"timed_out": True}, "could not be completed on the base"),
            ({}, {"error": "cannot run"}, "could not be completed on the base"),
            ({}, {"exit_code": 0}, "passes on the base revision"),
            ({}, {"exit_code": 2}, "exits 2 on the base revision, not 1"),
            ({}, {"output": "/missing.log"}, "could not be read"),
        ):
            with self.subTest(reason=reason):
                self.setUp()
                result = attribution.judge(
                    self.run_in("candidate", "E1\n", **candidate), self.run_in("base", "E1\n", **base)
                )
                self.assertEqual(("change", True), (result["kind"], reason in result["reason"]))

    def test_only_a_plain_command_is_eligible(self):
        self.assertTrue(attribution.eligible(".venv/bin/ruff check tools/ tests/ scenarios/"))
        for command in ("python3 -m unittest tests.test_x", "python -m pytest -q", "go test ./...", "", None):
            with self.subTest(command=command):
                self.assertFalse(attribution.eligible(command))


class BaseRunnerTests(unittest.TestCase):
    def test_the_runner_runs_on_the_proofs_original_code_under_the_launch_input_guard(self):
        calls = []
        with (
            mock.patch.object(regression, "reviewed_patch", return_value=Path("/reviewed.patch")),
            mock.patch.object(regression.launch_inputs, "guard", return_value=None),
            mock.patch.object(base_command, "run", side_effect=lambda *a, **k: calls.append((a, k)) or {}),
        ):
            runner = regression.clean_base_run({"base_commit": "abc"}, "/ws", "/run")
            runner("/ws", "/run/base-01", command="make lint", timeout=5)
        [(arguments, options)] = calls
        self.assertEqual(("/ws", "/run/base-01"), arguments)
        self.assertEqual(
            ("abc", Path("/reviewed.patch"), "make lint"), (options["base"], options["base_patch"], options["command"])
        )

    def test_no_base_an_operator_patch_a_native_owner_or_progressive_checks_compare_nothing(self):
        with mock.patch.object(regression.run_base, "base_commit", return_value=None):
            self.assertIsNone(regression.clean_base_run({}, "/ws", "/run"))
        with mock.patch.object(regression.operator_patch, "pinned", return_value={"path": "op.patch"}):
            self.assertIsNone(regression.clean_base_run({"base_commit": "abc"}, "/ws", "/run"))
        self.assertIsNone(regression.clean_base_run({"base_commit": "abc"}, "/ws", "/run", native=object()))
        self.assertIsNone(regression.clean_base_run({"base_commit": "abc"}, "/ws", "/run", progressive={"x": 1}))


class ReportTests(unittest.TestCase):
    def test_the_status_view_shows_the_attribution_with_the_check(self):
        record = {"kind": "base", "reason": "The check fails identically on the base revision"}
        state = {
            "validation": {
                "check_replay": {"verdict": "PASS", "checks": [{"command": "make lint", "base_attribution": record}]}
            }
        }
        [row] = run_view.evidence(state)["check_replay"]["checks"]
        self.assertEqual(record, row["base_attribution"])

    def test_the_report_says_whose_failure_a_failed_check_is(self):
        view = {
            "evidence": {
                "check_replay": {
                    "checks": [
                        {"command": "ruff check .", "exit_code": 1, "base_attribution": {"kind": "base"}},
                        {"command": "make lint", "exit_code": 1, "base_attribution": {"kind": "change"}},
                    ]
                }
            }
        }
        unverified = document.build(
            view, run_identity="r", completed_at=None, provenance=provenance.configured({}, "fake"), binding="b"
        )["unverified"]
        self.assertIn("Check fails identically on the base revision, attributed to the base: ruff check .", unverified)
        self.assertIn("Check failed or unavailable: make lint", unverified)
        self.assertNotIn("Check failed or unavailable: ruff check .", unverified)


if __name__ == "__main__":
    unittest.main()
