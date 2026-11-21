"""Failure equivalence reads authenticated per-test details, not only IDs."""

import hashlib
import tempfile
import unittest
from pathlib import Path

import autocode_failure_provenance as evidence


class FailureCaptureTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.output = self.root / "suite.log"
        self.output.write_text("runner output\n")
        self.report = self.root / "suite.xml"

    def receipt(self, content, *, command="python3 -m pytest tests"):
        self.report.write_text(content)
        return {
            "command": command,
            "exit_code": 1,
            "timed_out": False,
            "output": str(self.output),
            "output_sha256": hashlib.sha256(self.output.read_bytes()).hexdigest(),
            "results_capture": {
                "path": str(self.report),
                "sha256": hashlib.sha256(self.report.read_bytes()).hexdigest(),
            },
            "results": {"passed": [], "failed": ["test_x.TestX::test_a"], "skipped": [], "complete": True, "total": 1},
        }

    def test_volatile_pytest_instance_address_does_not_hide_exception_or_assertion_changes(self):
        def report(address, exception="missing dependency"):
            return (
                '<testsuite><testcase classname="test_x.TestX" name="test_a">'
                f'<failure message="{exception}">self = &lt;test_x.TestX object at {address}&gt;\n\n'
                f"    def test_a(self):\n&gt;       assert result\nE {exception}</failure></testcase></testsuite>"
            )

        before = evidence.observations(self.receipt(report("0x111")), root=self.root)
        same = evidence.observations(self.receipt(report("0x222")), root=self.root)
        different = evidence.observations(self.receipt(report("0x222", "different exception")), root=self.root)
        self.assertIsNotNone(before)
        self.assertEqual(before, same)
        self.assertNotEqual(before, different)

    def test_pytest_message_resembling_a_self_address_is_not_discarded(self):
        def report(address):
            return (
                '<testsuite><testcase classname="test_x.TestX" name="test_a">'
                f'<failure message="constant">self = &lt;test_x.TestX object at {address}&gt;'
                "</failure></testcase></testsuite>"
            )

        before = evidence.observations(self.receipt(report("0x111")), root=self.root)
        different = evidence.observations(self.receipt(report("0x222")), root=self.root)
        self.assertIsNotNone(before)
        self.assertNotEqual(before, different)

    def test_same_outer_error_with_a_different_chained_cause_is_not_identical(self):
        def report(cause):
            return (
                '<testsuite><testcase classname="test_x.TestX" name="test_a">'
                '<failure message="RuntimeError: same">'
                f"E ValueError: {cause}\n"
                "The above exception was the direct cause of the following exception:\n"
                "E RuntimeError: same</failure></testcase></testsuite>"
            )

        before = evidence.observations(self.receipt(report("database unavailable")), root=self.root)
        changed = evidence.observations(self.receipt(report("authorization denied")), root=self.root)
        self.assertIsNotNone(before)
        self.assertNotEqual(before, changed)

    def test_duplicate_or_wrong_test_and_doctype_are_not_failure_evidence(self):
        one = '<testcase classname="test_x.TestX" name="test_a"><failure message="bad">bad</failure></testcase>'
        for content in (
            f"<testsuite>{one}{one}</testsuite>",
            "<testsuite><testcase classname='other' name='test_a'><failure>bad</failure></testcase></testsuite>",
            "<testsuite><testcase classname='test_x.TestX' name='test_a'><failure message='bad'/></testcase></testsuite>",
            "<testsuite><testcase classname='test_x.TestX' name='test_a'><failure>bad</failure></testcase></testsuite>",
            f'<!DOCTYPE testsuite [<!ENTITY secret "bad">]><testsuite>{one}</testsuite>',
        ):
            with self.subTest(content=content):
                self.assertIsNone(evidence.observations(self.receipt(content), root=self.root))

    def test_tampered_report_cannot_authorize_equivalence(self):
        content = (
            '<testsuite><testcase classname="test_x.TestX" name="test_a"><failure>bad</failure></testcase></testsuite>'
        )
        receipt = self.receipt(content)
        self.report.write_text(content.replace("bad", "different"))
        self.assertIsNone(evidence.observations(receipt, root=self.root))

    def test_unittest_trace_root_is_normalized_but_exception_message_is_not(self):
        def observe(root):
            text = (
                "test_a (test_x.TestX) ... FAIL\n" + "=" * 70 + "\n"
                "FAIL: test_a (test_x.TestX)\n" + "-" * 70 + "\n"
                "Traceback (most recent call last):\n"
                f'  File "{root}/test_x.py", line 2, in test_a\n    self.assertTrue(False)\n'
                f'AssertionError: failed\n  File "{root}/message", line 1, in claimed\n'
                + "-" * 70
                + "\nRan 1 test in 0.01s\n\nFAILED (failures=1)\n"
            )
            self.output.write_text(text)
            receipt = {
                "command": "python3 -m unittest -v test_x",
                "exit_code": 1,
                "timed_out": False,
                "execution_root": root,
                "output": str(self.output),
                "output_sha256": hashlib.sha256(self.output.read_bytes()).hexdigest(),
                "results": {
                    "passed": [],
                    "failed": ["test_x.TestX::test_a"],
                    "skipped": [],
                    "complete": True,
                    "total": 1,
                },
            }
            return evidence.observations(receipt, root=self.root)

        before, different = observe("/scratch/base"), observe("/scratch/candidate")
        self.assertIsNotNone(before)
        self.assertNotEqual(before, different)

    def test_project_scope_refuses_shell_programs_and_distinguishes_nested_runners(self):
        self.assertEqual("", evidence._project({"command": "python3 -B -m unittest discover -v"}))
        self.assertEqual("api", evidence._project({"command": "cd api && python3 -m pytest tests"}))
        self.assertEqual("", evidence._project({"command": "go test -json ./..."}))
        self.assertEqual("api", evidence._project({"command": "cd api && go test -json ./..."}))
        for command in (
            "python3 -m pytest tests; cd api",
            "python3 -m pytest tests && cd api",
            "python3 -m pytest $(other-command)",
            "cd api; python3 -m pytest tests",
            "npm test",
            "go test -C api ./...",
            "go test -exec custom ./...",
            "env GOFLAGS=-race go test ./...",
        ):
            with self.subTest(command=command):
                self.assertIsNone(evidence._project({"command": command}))

    def test_go_events_compare_failure_details_not_elapsed_time(self):
        import json

        def observe(message, elapsed):
            rows = [
                {"Action": "run", "Package": "example.org/pkg", "Test": "TestA"},
                {"Action": "output", "Package": "example.org/pkg", "Test": "TestA", "Output": "=== RUN   TestA\n"},
                {
                    "Action": "output",
                    "Package": "example.org/pkg",
                    "Test": "TestA",
                    "Output": f"    x_test.go:3: {message}\n",
                },
                {
                    "Action": "output",
                    "Package": "example.org/pkg",
                    "Test": "TestA",
                    "Output": f"--- FAIL: TestA ({elapsed}s)\n",
                },
                {"Action": "fail", "Package": "example.org/pkg", "Test": "TestA"},
            ]
            self.output.write_text("\n".join(json.dumps(row) for row in rows))
            receipt = {
                "command": "go test -json ./...",
                "exit_code": 1,
                "timed_out": False,
                "output": str(self.output),
                "output_sha256": hashlib.sha256(self.output.read_bytes()).hexdigest(),
                "results": {
                    "passed": [],
                    "failed": ["example.org/pkg::TestA"],
                    "skipped": [],
                    "complete": True,
                    "total": 1,
                },
            }
            return evidence.observations(receipt, root=self.root)

        before, same, different = observe("missing", "0.01"), observe("missing", "1.23"), observe("other", "1.23")
        self.assertIsNotNone(before)
        self.assertEqual(before, same)
        self.assertNotEqual(before, different)

    def test_over_nested_proof_is_refused_without_crashing(self):
        directory = self.root / "regression"
        directory.mkdir()
        path = directory / "proof.json"
        path.write_text("[" * 2000 + "]" * 2000)
        proof = {
            "verdict": "PASS",
            "source_revision": "revision",
            "path": str(path),
            "receipt_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "execution_context": {"test_role_policy": evidence.test_roles.VERSION, "protected_test_binding": "binding"},
        }
        state = {
            "run_dir": str(self.root),
            "settings": {"protected_tests": {"binding_hash": "binding"}},
            "regression_proof": proof,
        }
        self.assertIsNone(evidence.baseline(state, "revision"))

    def test_malformed_multi_suite_capture_is_refused_without_crashing(self):
        self.assertIsNone(evidence.observations({"suites": [None]}, root=self.root))


if __name__ == "__main__":
    unittest.main()
