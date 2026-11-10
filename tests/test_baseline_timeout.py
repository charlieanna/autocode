"""A returned baseline timeout records an unverified proof without candidate commands."""

import json
import shlex
import sys
import unittest
from pathlib import Path
from unittest import mock

import autocode_command_receipt as receipts
import autocode_regression as regression
import autocode_util as util
import autocode_verify as verify

from .test_command_receipt import guarded_command_receipt
from .test_verify import Project

KEEP = "test_suite.Suite.test_keep"
OLD = "test_suite.Suite.test_old_failure"
NEW = "test_regression.Regression.test_add"


class BaselineTimeoutTests(unittest.TestCase):
    def setUp(self):
        self.project = Project(
            {
                "calc.py": "def add(a, b): return a - b\n",
                "test_suite.py": "import unittest\nclass Suite(unittest.TestCase):\n"
                " def test_keep(self): self.assertEqual(2, 1 + 1)\n"
                " def test_old_failure(self): self.assertEqual(3, 1 + 1)\n",
            }
        )
        self.addCleanup(self.project.close)
        self.project.write(
            {
                "calc.py": "def add(a, b): return a + b\n",
                "test_regression.py": "import unittest\nfrom calc import add\nclass Regression(unittest.TestCase):\n"
                " def test_add(self): self.assertEqual(5, add(2, 3))\n",
            }
        )
        self.calls = []

    def receipt(self, command, directory, label, *, timeout=False, complete=True, passed=(), failed=()):
        output = Path(directory) / (label + ".log")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("timeout\n" if timeout else "completed\n")
        return guarded_command_receipt(
            {
                "command": command,
                "exit_code": None if timeout else 1 if failed else 0,
                "timed_out": timeout,
                "output": str(output),
                "output_sha256": util.file_hash(output),
                "tail": "",
                "results_expected": True,
                "results": {
                    "passed": list(passed),
                    "failed": list(failed),
                    "skipped": [],
                    "collection_errors": [],
                    "uncollected": [],
                    "total": len(passed) + len(failed),
                    "complete": complete,
                },
            }
        )

    def test_authoritative_timeout_prevents_all_candidate_commands_even_after_complete_output(self):
        for complete in (False, True):
            with self.subTest(complete_output=complete):
                self.calls.clear()

                def run(_framework, command, _tree, directory, label, **_kwargs):
                    self.calls.append(label)
                    self.assertEqual("suite-on-base", label, "No command may follow the timed-out baseline")
                    return self.receipt(command, directory, label, timeout=True, complete=complete, passed=[KEEP])

                with mock.patch.object(verify, "run_suite", side_effect=run):
                    result = self.project.verify()
                self.assertEqual(["suite-on-base"], self.calls)
                self.assertEqual(verify.UNVERIFIED, result["verdict"], result)
                self.assertEqual("timeout", result["baseline"]["health"])
                self.assertEqual({"suite_on_base"}, set(result["checks"]))
                self.assertTrue(result["checks"]["suite_on_base"]["timed_out"])
                self.assertTrue(any("candidate checks were not started" in reason for reason in result["unverified"]))
                self.assertFalse(any("No command to run" in reason for reason in result["unverified"]))
                self.assertFalse(any("No project test command" in reason for reason in result["unverified"]))
                self.assertIsNone(result["fail_to_pass"])

    def test_complete_failing_baseline_still_runs_candidate_commands(self):
        def run(_framework, command, _tree, directory, label, **_kwargs):
            self.calls.append(label)
            if label == "regression-on-base":
                passed, failed = [], [NEW]
            elif label == "regression-on-candidate":
                passed, failed = [NEW], []
            else:
                passed, failed = [KEEP], [OLD]
                if label == "suite-on-candidate":
                    passed.append(NEW)
            return self.receipt(command, directory, label, passed=passed, failed=failed)

        with (
            mock.patch.object(verify, "run_suite", side_effect=run),
            mock.patch.object(verify.diff_coverage, "collect", return_value=None),
        ):
            result = self.project.verify()
        self.assertIn("suite-on-candidate", self.calls)
        self.assertIn("regression-on-candidate", self.calls)
        self.assertEqual("failing_tests", result["baseline"]["health"])
        self.assertEqual(verify.PASS, result["verdict"], result)
        self.assertEqual([NEW], result["fail_to_pass"])

    def test_uncertain_custody_keeps_its_existing_refusal(self):
        def run(_framework, _command, _tree, _directory, label, **_kwargs):
            self.calls.append(label)
            raise receipts.OwnershipUncertain("Fixture cleanup remains uncertain")

        with (
            mock.patch.object(verify, "run_suite", side_effect=run),
            self.assertRaises(receipts.OwnershipUncertain) as stopped,
        ):
            self.project.verify()
        self.assertEqual("PAUSED_VERIFICATION_UNCERTAIN", stopped.exception.status)
        self.assertEqual(["suite-on-base"], self.calls)

    def test_public_proof_records_timeout_without_internal_retry_and_allows_later_baseline_refresh(self):
        run_dir = self.project.root / ".autocode" / "runs" / "proof"
        run_dir.mkdir(parents=True)
        state = {
            "workspace": str(self.project.root),
            "base_commit": self.project.base,
            "iteration": 1,
            "stages": [],
            "history": [],
            "settings": {"regression": {"test_command": shlex.quote(sys.executable) + " -m unittest -v"}},
            "goal_contract": {"body": {"task_kind": "bugfix"}},
        }

        def run(_framework, command, _tree, directory, label, **_kwargs):
            self.calls.append(label)
            self.assertEqual("suite-on-base", label)
            return self.receipt(command, directory, label, timeout=True, complete=False, passed=[KEEP])

        with mock.patch.object(verify, "run_suite", side_effect=run):
            proof = regression.prove(state, self.project.root, run_dir)
            regression.before_review(state, "review_completion", self.project.root, run_dir)
        self.assertEqual(["suite-on-base"], self.calls, "Review must receive the unverified result without a retry")
        self.assertEqual(verify.UNVERIFIED, proof["verdict"])
        recorded = json.loads(Path(proof["path"]).read_text())
        self.assertEqual("timeout", recorded["baseline"]["health"])
        self.assertTrue(recorded["checks"]["suite_on_base"]["timed_out"])
        self.assertEqual(proof["source_revision"], recorded["source_revision"])
        self.assertFalse(regression.complete(state, proof["source_revision"]))
        # A later, explicitly initiated retry uses the existing refresh path. A saved
        # timeout is not a permanent cache hit and does not require a settings change.
        with (
            mock.patch.object(verify, "baseline", side_effect=RuntimeError("fresh baseline reached")) as refreshed,
            self.assertRaisesRegex(RuntimeError, "fresh baseline reached"),
        ):
            regression.prove(state, self.project.root, run_dir)
        refreshed.assert_called_once()


if __name__ == "__main__":
    unittest.main()
