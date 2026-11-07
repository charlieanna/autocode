"""CI attribution identifies the actual failed checks, including serial runs."""

import io
import json
import tempfile
import unittest
from pathlib import Path

import ci_failure_signatures as evidence

from .ci_failure_fixture import failure_output


class FailureSignaturesTests(unittest.TestCase):
    def test_same_assertion_is_stable_across_checkout_paths_and_shifted_lines(self):
        head = evidence.signatures(failure_output("/head", line=17), Path("/head"))
        base = evidence.signatures(failure_output("/base", line=12), Path("/base"))
        self.assertEqual(head, base)
        self.assertEqual("tests.test_value.ValueTests.test_value", head[0]["test"])

    def test_different_assertions_and_test_identities_remain_distinct(self):
        old = evidence.signatures(failure_output("/base"), Path("/base"))
        changed = evidence.signatures(failure_output("/base", message="new failure"), Path("/base"))
        other = evidence.signatures(failure_output("/base", method="test_other"), Path("/base"))
        self.assertNotEqual(old, changed)
        self.assertNotEqual(old, other)

    def test_multiple_failures_and_python_314_test_descriptions_are_preserved(self):
        output = failure_output("/base") + failure_output("/base", method="test_other").replace(
            "test_other (tests.test_value.ValueTests)",
            "test_other (tests.test_value.ValueTests.test_other)",
        )
        rows = evidence.signatures(output, Path("/base"))
        self.assertEqual(
            ["tests.test_value.ValueTests.test_value", "tests.test_value.ValueTests.test_other"],
            [row["test"] for row in rows],
        )

    def test_infrastructure_output_is_not_a_test_failure_signature(self):
        self.assertEqual([], evidence.signatures("python could not start", Path("/base")))
        self.assertEqual([], evidence.signatures("Ran 3 tests\n\nOK", Path("/base")))

    def test_report_records_only_failed_modules_and_their_failure_identities(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "failed.json"
            evidence.write(
                output,
                [
                    {"module": "tests.test_value", "ok": False, "output": failure_output(str(root))},
                    {"module": "tests.test_good", "ok": True, "output": "OK"},
                ],
                root,
            )
            report = json.loads(output.read_text())
            self.assertEqual(1, report["version"])
            self.assertEqual(["tests.test_value"], [row["module"] for row in report["failures"]])
            self.assertEqual("tests.test_value.ValueTests.test_value", report["failures"][0]["failures"][0]["test"])

    def test_serial_result_preserves_failure_error_and_unexpected_success(self):
        case = unittest.FunctionTestCase(lambda: None)
        result = unittest.TestResult()
        result.failures = [(case, "AssertionError: bad")]
        result.errors = [(case, "ValueError: bad")]
        result.unexpectedSuccesses = [case]
        rows = evidence.result_rows(result)
        self.assertEqual(["unittest.case"], [row["module"] for row in rows])
        signatures = evidence.signatures(rows[0]["output"], Path("/head"))
        self.assertEqual(["FAIL", "ERROR", "UNEXPECTED SUCCESS"], [row["kind"] for row in signatures])

    def test_serial_class_setup_error_uses_the_owning_module(self):
        class Broken(unittest.TestCase):
            @classmethod
            def setUpClass(cls):
                raise RuntimeError("setup broke")

            def test_example(self):
                pass

        output = io.StringIO()
        result = unittest.TextTestRunner(stream=output).run(unittest.defaultTestLoader.loadTestsFromTestCase(Broken))
        rows = evidence.result_rows(result)
        self.assertEqual(__name__, rows[0]["module"])
        self.assertEqual(
            evidence.signatures(output.getvalue(), Path.cwd()), evidence.signatures(rows[0]["output"], Path.cwd())
        )

    def test_serial_subtest_has_the_same_signature_as_child_output(self):
        class Broken(unittest.TestCase):
            def test_example(self):
                with self.subTest(value=2):
                    self.fail("subtest broke")

        output = io.StringIO()
        result = unittest.TextTestRunner(stream=output).run(unittest.defaultTestLoader.loadTestsFromTestCase(Broken))
        rows = evidence.result_rows(result)
        self.assertEqual(
            evidence.signatures(output.getvalue(), Path.cwd()), evidence.signatures(rows[0]["output"], Path.cwd())
        )
