"""The attack reporter must never turn missing/failed outcomes into success."""
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from . import adversarial
from .adversarial import Result, execute, group_passed
from .harness.attempts import atomic_json
from .harness.processes import CallTimeout


class ReporterTests(unittest.TestCase):
    def run_cases(self, cases):
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(cases)
        return unittest.TextTestRunner(stream=io.StringIO(), resultclass=Result).run(suite)

    def test_failed_subtest_remains_visible_beside_a_passing_case(self):
        class Cases(unittest.TestCase):
            def test_pass(self):
                self.assertEqual(2, 1 + 1)

            def test_subtest(self):
                with self.subTest(attack="bad evidence"):
                    self.fail("invariant broken")

        result = self.run_cases(Cases)
        self.assertEqual(2, result.testsRun)
        self.assertCountEqual(["FAIL", "PASS"], [row["status"] for row in result.rows])
        self.assertFalse(group_passed({"tests_run": result.testsRun, "rows": result.rows, "exit_code": 1}))

    def test_cleanup_error_and_assertion_failure_are_one_failed_case(self):
        class Cases(unittest.TestCase):
            def test_bad(self):
                self.fail("bad completion")

            def tearDown(self):
                raise RuntimeError("orphan cleanup failed")

        result = self.run_cases(Cases)
        self.assertEqual(1, len(result.rows))
        self.assertEqual("ERROR", result.rows[0]["status"])
        self.assertIn("bad completion", result.rows[0]["detail"])
        self.assertIn("orphan cleanup failed", result.rows[0]["detail"])

    def test_failed_worker_or_missing_outcome_cannot_pass(self):
        passed = {"tests_run": 1, "rows": [{"status": "PASS"}], "exit_code": 0}
        self.assertTrue(group_passed(passed))
        self.assertFalse(group_passed({**passed, "exit_code": 1}))
        self.assertFalse(group_passed({**passed, "tests_run": 2}))
        self.assertFalse(group_passed({"tests_run": 0, "rows": [], "exit_code": 0}))

    def test_worker_checkpoints_each_outcome_and_publishes_final_atomically(self):
        case = unittest.FunctionTestCase(lambda: None)
        observed = []
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            def run(runner, suite):
                result = runner._makeResult()
                result.startTest(case)
                result.addSuccess(case)
                observed.append(json.loads((output / "result.json").read_text()))
                result.add_row(case, "ERROR", "cleanup failed")
                observed.append(json.loads((output / "result.json").read_text()))
                result.stopTest(case)
                return result
            with patch.dict(os.environ), patch.object(adversarial.unittest.defaultTestLoader,
                    "loadTestsFromName", return_value=unittest.TestSuite()), \
                    patch.object(adversarial.unittest.TextTestRunner, "run", run), \
                    patch.object(adversarial, "atomic_json", wraps=atomic_json) as publish:
                self.assertEqual(1, adversarial.worker("evidence", output))
            self.assertEqual(3, publish.call_count)
            self.assertEqual(["PASS", "ERROR"], [report["rows"][0]["status"] for report in observed])
            final = json.loads((output / "result.json").read_text())
            self.assertEqual(observed[-1], final)
            self.assertEqual(1, final["tests_run"])
            self.assertEqual(["result.json"], [p.name for p in output.iterdir()])

    def test_timeout_retains_completed_outcomes_output_and_cleanup_diagnostics(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            rows = [{"test": "completed.pass", "status": "PASS", "detail": "", "evidence": None},
                    {"test": "completed.fail", "status": "FAIL", "detail": "invariant", "evidence": None}]
            atomic_json(output / "evidence/result.json", {"group": "evidence", "tests_run": 2, "rows": rows})
            error = CallTimeout(["worker"], 7, ["owned PID 101 remains alive"],
                                output=b"completed output\n", stderr=b"worker error\n")
            with patch("scenarios.harness.processes.run_cli", side_effect=error) as call:
                report = execute("evidence", output, 7)
            self.assertEqual(7, call.call_args.kwargs["timeout"])
            self.assertEqual(2, report["tests_run"])
            self.assertEqual(rows, report["rows"][:2])
            self.assertEqual("ERROR", report["rows"][-1]["status"])
            self.assertEqual("evidence", report["rows"][-1]["test"])
            self.assertIn("timed out", report["rows"][-1]["detail"])
            self.assertIn("owned PID 101 remains alive", report["rows"][-1]["detail"])
            self.assertEqual(error.cleanup_errors, report["cleanup_errors"])
            self.assertFalse(group_passed(report))
            self.assertEqual(2, report["exit_code"])
            self.assertEqual(report, json.loads((output / "evidence/result.json").read_text()))
            console = (output / "evidence/console.log").read_text()
            for text in ("completed output", "worker error", "owned PID 101 remains alive"):
                self.assertIn(text, console)

    def test_timeout_before_first_outcome_is_durable_and_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            with patch("scenarios.harness.processes.run_cli", side_effect=CallTimeout(["worker"], 600, [])):
                report = execute("recovery", output)
            self.assertEqual(0, report["tests_run"])
            self.assertEqual(["ERROR"], [row["status"] for row in report["rows"]])
            self.assertFalse(group_passed(report))
            self.assertEqual(report, json.loads((output / "recovery/result.json").read_text()))

    def test_returned_worker_output_survives_missing_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            returned = subprocess.CompletedProcess(["worker"], 1, "stdout retained\n", "stderr retained\n")
            with patch("scenarios.harness.processes.run_cli", return_value=returned):
                report = execute("recovery", output)
            self.assertFalse(group_passed(report))
            console = (output / "recovery/console.log").read_text()
            self.assertIn("stdout retained", console)
            self.assertIn("stderr retained", console)
            self.assertIn("Cannot read outcome checkpoint", console)

    def test_malformed_checkpoint_is_a_durable_error_on_return_or_timeout(self):
        invalid = [{}, [], None, {"group": "evidence", "tests_run": 1, "rows": None},
                   {"group": "evidence", "tests_run": True, "rows": []},
                   {"group": "other", "tests_run": 0, "rows": []},
                   {"group": "evidence", "tests_run": 1, "rows": [{"test": "case", "status": "UNKNOWN"}]}]
        for checkpoint in invalid:
            for timeout in (False, True):
                with self.subTest(checkpoint=checkpoint, timeout=timeout), tempfile.TemporaryDirectory() as tmp:
                    output = Path(tmp)
                    atomic_json(output / "evidence/result.json", checkpoint)
                    response = {"side_effect": CallTimeout(["worker"], 600, [])} if timeout else {
                        "return_value": subprocess.CompletedProcess(["worker"], 0, "retained output", "")}
                    with patch("scenarios.harness.processes.run_cli", **response):
                        report = execute("evidence", output)
                    self.assertFalse(group_passed(report))
                    self.assertEqual(2, report["exit_code"])
                    self.assertEqual(["ERROR"], [row["status"] for row in report["rows"]])
                    self.assertIn("Invalid outcome checkpoint", report["rows"][0]["detail"])
                    self.assertEqual(report, json.loads((output / "evidence/result.json").read_text()))

    def test_cli_timeout_default_and_override_reach_group_and_error_stays_nonzero(self):
        for flags, timeout in (([], 600), (["--group-timeout-seconds", "9"], 9)):
            with self.subTest(timeout=timeout), tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp) / "run"
                report = {"group": "evidence", "tests_run": 1, "exit_code": 2,
                          "rows": [{"test": "done", "status": "PASS", "evidence": None},
                                   {"test": "evidence", "status": "ERROR", "evidence": None}]}
                with patch.object(adversarial.sys, "argv", ["adversarial", "--group", "evidence",
                        "--out", str(output), *flags]), \
                        patch.object(adversarial, "execute", return_value=report) as run, \
                        patch.object(adversarial, "source_hashes", return_value={}), \
                        patch.object(adversarial.subprocess, "check_output", return_value="pinned\n"), \
                        patch.object(adversarial.subprocess, "run", return_value=Mock(returncode=0)), \
                        patch("sys.stdout", new=io.StringIO()):
                    self.assertEqual(1, adversarial.main())
                run.assert_called_once_with("evidence", output.resolve(), timeout)
                summary = json.loads((output / "summary.json").read_text())
                self.assertEqual(1, summary["tests_run"])
                self.assertEqual(1, summary["counts"]["PASS"])
                self.assertEqual(1, summary["counts"]["ERROR"])
                self.assertEqual(["evidence"], summary["group_execution_failures"])

    def test_cli_rejects_nonpositive_or_noninteger_timeout_before_launch(self):
        for value in ("0", "-1", "1.5", "invalid"):
            with self.subTest(value=value), patch.object(adversarial.sys, "argv",
                    ["adversarial", "--group-timeout-seconds", value]), \
                    patch.object(adversarial, "execute") as run, patch("sys.stderr", new=io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    adversarial.main()
                self.assertEqual(2, caught.exception.code)
                run.assert_not_called()
