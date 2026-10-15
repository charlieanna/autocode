"""The explicit JUnit adapter preserves owned execution and fresh sidecars."""

import hashlib
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_junit as junit
import autocode_junit_tests as adapter
from autocode_test_framework import Framework

REPORT = '<testsuite name="Example" tests="1"><testcase name="works" time="0.125"/></testsuite>'


class JUnitExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.tree = self.root / "project"
        self.evidence = self.root / "evidence"
        self.tree.mkdir()
        self.evidence.mkdir()
        self.relative = "target/surefire-reports/TEST-Example.xml"
        self.report = self.tree / self.relative
        self.framework = adapter.JUnitFramework("mvn -q test", "junit:" + self.relative)
        self.tracked = mock.Mock(return_value=False)

    def executor(self, report=REPORT, *, exit_code=0, **extra):
        def execute(command, tree, log, *, timeout):
            self.assertFalse(self.report.exists(), "the old report must be cleared before command execution")
            log.write_text("runner output\n")
            if report is not None:
                self.report.parent.mkdir(parents=True, exist_ok=True)
                self.report.write_text(report)
            return {
                "command": command,
                "output": str(log),
                "output_sha256": hashlib.sha256(log.read_bytes()).hexdigest(),
                "exit_code": exit_code,
                "timed_out": False,
                "duration_seconds": 0.25,
                "tail": "runner output\n",
                **extra,
            }

        return mock.Mock(side_effect=execute)

    def run_adapter(self, execute, *, command=None, label="candidate", tracked=None):
        return adapter.run_suite(
            self.framework,
            command or self.framework.suite,
            self.tree,
            self.evidence,
            label,
            timeout=23,
            execute=execute,
            is_tracked=self.tracked if tracked is None else tracked,
        )

    def test_framework_is_explicit_and_reuses_the_exact_command(self):
        command = "env MODE=checks mvn -q -Dtest=Example test"
        framework = adapter.JUnitFramework(command, "junit:./target//result.xml")
        self.assertIsInstance(framework, Framework)
        self.assertEqual(framework.name, "junit")
        self.assertEqual(framework.suite, command)
        self.assertIs(framework.per_test, True)
        self.assertEqual(framework.targeted(["src/test/java/ExampleTest.java"]), command)
        self.assertIsNone(framework.targeted([]))
        self.assertIsNone(framework.targeted(iter(())))
        self.assertEqual(
            framework.to_dict(),
            {"name": "junit", "suite": command, "python": None, "note": "", "test_report": "junit:target/result.xml"},
        )
        for invalid in (None, "", "  "):
            with self.subTest(command=invalid), self.assertRaises(ValueError):
                adapter.JUnitFramework(invalid, "junit:report.xml")
        with self.assertRaises(ValueError):
            adapter.JUnitFramework(command, "other:report.xml")

    def test_fresh_report_sidecar_preserves_exact_owned_execution(self):
        execute = self.executor(supervision={"receipt": "owned-process.json"}, supervision_errors=[])
        command = "mvn -q -Dtest=Example#works test"
        receipt = self.run_adapter(execute, command=command)
        self.tracked.assert_called_once_with(self.relative)
        called_command, called_tree, log = execute.call_args.args
        self.assertEqual(called_command, command)
        self.assertEqual(called_tree, self.tree)
        self.assertEqual(execute.call_args.kwargs, {"timeout": 23})
        self.assertEqual(receipt["command"], command)
        self.assertEqual(receipt["supervision"], {"receipt": "owned-process.json"})
        self.assertEqual(receipt["supervision_errors"], [])
        self.assertEqual(receipt["exit_code"], 0)
        self.assertEqual(receipt["duration_seconds"], 0.25)
        self.assertEqual(receipt["output"], str(log))
        self.assertEqual(receipt["results_format"], "junit")
        self.assertIs(receipt["results_expected"], True)
        self.assertEqual(receipt["report_status"], "captured")
        self.assertEqual(receipt["results"]["passed"], ["Example::works"])
        self.assertEqual(receipt["results"]["durations"], {"Example::works": 0.125})
        artifact = Path(receipt["test_report"]["path"])
        self.assertEqual(artifact.parent, self.evidence)
        self.assertRegex(artifact.name, r"^candidate\.[0-9a-f]{32}\.junit\.xml$")
        self.assertEqual(artifact.read_text(), REPORT)
        self.assertTrue(junit.intact(receipt, root=self.evidence))

    def test_stale_xml_is_cleared_and_absence_is_not_a_process_error(self):
        self.report.parent.mkdir(parents=True)
        self.report.write_text(REPORT)
        receipt = self.run_adapter(self.executor(None, exit_code=1))
        self.assertFalse(self.report.exists())
        self.assertEqual(receipt["report_status"], "missing")
        self.assertIn("report_error", receipt)
        self.assertIsNone(receipt["results"])
        self.assertNotIn("test_report", receipt)
        self.assertNotIn("error", receipt)
        self.assertEqual(receipt["exit_code"], 1)
        self.assertIs(receipt["results_expected"], True)
        self.assertFalse(junit.intact(receipt, root=self.evidence))

    def test_missing_report_directories_are_an_absence_observation(self):
        receipt = self.run_adapter(self.executor(None))
        self.assertEqual(receipt["report_status"], "missing")
        self.assertFalse(self.report.parent.exists())
        self.assertNotIn("error", receipt)
        self.assertIsNone(receipt["results"])

    def test_malformed_xml_is_invalid_even_when_the_command_exits_zero(self):
        receipt = self.run_adapter(self.executor('<testsuite name="truncated">'))
        self.assertEqual(receipt["report_status"], "invalid")
        self.assertEqual(receipt["exit_code"], 0)
        self.assertNotIn("error", receipt)
        self.assertNotIn("test_report", receipt)
        self.assertIsNone(receipt["results"])
        self.assertTrue(receipt["report_error"])
        self.assertFalse(junit.intact(receipt, root=self.evidence))

    def test_tracked_output_refuses_execution_and_preserves_source(self):
        self.report.parent.mkdir(parents=True)
        self.report.write_text("tracked source")
        execute = self.executor()
        receipt = self.run_adapter(execute, tracked=mock.Mock(return_value=True))
        execute.assert_not_called()
        self.assertIs(receipt["executed"], False)
        self.assertIsNone(receipt["exit_code"])
        self.assertEqual(receipt["report_status"], "invalid")
        self.assertIn("tracked source", self.report.read_text())
        self.assertTrue(receipt["error"])
        self.assertEqual(receipt["output_sha256"], hashlib.sha256(Path(receipt["output"]).read_bytes()).hexdigest())

    def test_unknown_tracked_inventory_cannot_authorize_a_launch(self):
        execute = self.executor()
        for tracked in (mock.Mock(return_value=None), mock.Mock(side_effect=RuntimeError("git inventory unavailable"))):
            with self.subTest(tracked=tracked):
                receipt = self.run_adapter(execute, tracked=tracked)
                self.assertIs(receipt["executed"], False)
                self.assertTrue(receipt["error"])
        execute.assert_not_called()

    def test_symlinked_output_is_refused_before_execution(self):
        outside = self.root / "outside.xml"
        outside.write_text(REPORT)
        self.report.parent.mkdir(parents=True)
        self.report.symlink_to(outside)
        execute = self.executor()
        receipt = self.run_adapter(execute)
        execute.assert_not_called()
        self.assertIs(receipt["executed"], False)
        self.assertTrue(receipt["error"])
        self.assertEqual(outside.read_text(), REPORT)

    def test_dangling_symlink_after_execution_is_invalid_not_missing(self):
        execute = self.executor(None)

        def link_after_execution(*args, **kwargs):
            receipt = execute(*args, **kwargs)
            self.report.parent.mkdir(parents=True)
            self.report.symlink_to(self.root / "absent.xml")
            return receipt

        receipt = self.run_adapter(link_after_execution)
        self.assertEqual(receipt["report_status"], "invalid")
        self.assertIsNone(receipt["results"])
        self.assertNotIn("error", receipt)

    def test_fifo_after_execution_is_invalid_not_missing(self):
        execute = self.executor(None)

        def fifo_after_execution(*args, **kwargs):
            receipt = execute(*args, **kwargs)
            self.report.parent.mkdir(parents=True)
            os.mkfifo(self.report)
            return receipt

        receipt = self.run_adapter(fifo_after_execution)
        self.assertEqual(receipt["report_status"], "invalid")
        self.assertIsNone(receipt["results"])

    def test_evidence_write_failure_cannot_impersonate_missing_source(self):
        with mock.patch.object(junit.os, "replace", side_effect=FileNotFoundError("evidence directory moved")):
            receipt = self.run_adapter(self.executor())
        self.assertEqual(receipt["report_status"], "invalid")
        self.assertNotIn("error", receipt)
        self.assertIsNone(receipt["results"])

    def test_captured_report_survives_scratch_removal_and_detects_hand_edits(self):
        receipt = self.run_adapter(self.executor())
        shutil.rmtree(self.tree)
        self.assertTrue(junit.intact(receipt, root=self.evidence))
        artifact = Path(receipt["test_report"]["path"])
        artifact.write_text(REPORT.replace('name="works"', 'name="inventedPass"'))
        self.assertFalse(junit.intact(receipt, root=self.evidence))

    def test_same_label_has_independent_retained_artifacts(self):
        first = self.run_adapter(self.executor())
        second = self.run_adapter(self.executor(REPORT.replace('name="works"', 'name="another"')))
        self.assertNotEqual(first["test_report"]["path"], second["test_report"]["path"])
        self.assertNotEqual(first["output"], second["output"])
        self.assertTrue(junit.intact(first, root=self.evidence))
        self.assertTrue(junit.intact(second, root=self.evidence))

    def test_process_errors_and_exceptions_keep_their_ownership_meaning(self):
        receipt = self.run_adapter(
            self.executor(exit_code=None, timed_out=True, error="cleanup uncertain", supervision_errors=["keeper lost"])
        )
        self.assertEqual(receipt["error"], "cleanup uncertain")
        self.assertEqual(receipt["supervision_errors"], ["keeper lost"])
        self.assertIs(receipt["timed_out"], True)
        self.assertIsNone(receipt["exit_code"])
        self.assertEqual(receipt["report_status"], "captured")
        execute = mock.Mock(side_effect=RuntimeError("ownership admission refused"))
        with self.assertRaisesRegex(RuntimeError, "ownership admission refused"):
            self.run_adapter(execute)
        execute.assert_called_once()


if __name__ == "__main__":
    unittest.main()
