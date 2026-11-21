"""CI attribution identifies the actual failed checks, including serial runs."""

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import attribute_failure
import ci_failure_signatures as evidence

from .ci_failure_fixture import (
    ID_HEAD,
    LIST_CODE,
    LIST_DIFF,
    RUN_HEAD,
    SET_CODE,
    SET_DIFF,
    STRING_CODE,
    STRING_DIFF,
    TEMP_HEAD,
    VOLATILE_PAIRS,
    assertion_output,
    failure_output,
    ref_failure_output,
)


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

    def test_generated_start_refs_keep_the_same_failure_identity_across_fresh_commits(self):
        prefix = "refs/autocode/partial-work/start/"
        head = evidence.signatures(ref_failure_output("/head", prefix + "a" * 40, line=17), Path("/head"))
        base = evidence.signatures(ref_failure_output("/base", prefix + "b" * 40, line=12), Path("/base"))
        self.assertEqual(1, len(head))
        self.assertEqual("FAIL", head[0]["kind"])
        self.assertEqual("tests.test_value.ValueTests.test_value", head[0]["test"])
        self.assertEqual(head, base)

    def test_appended_ref_forms_and_commit_values_do_not_change_the_failure_identity(self):
        # Replaces test_other_ref_forms_and_commit_values_remain_distinct (AC1 supersedes it): the ref is the
        # message assertTrue appended after " : ", not what the assertion compared, whatever its form.
        for form in (
            "{}",
            "refs/autocode/code-checkpoints/{}",
            "refs/autocode/partial-work/completed/{}",
            "refs/other/partial-work/start/{}",
            "other/refs/autocode/partial-work/start/{}",
            "refs/autocode/partial-work/start/{}_child",
            "refs/autocode/partial-work/start/{}/child",
            "refs/autocode/partial-work/start/{}.",
            "'refs/autocode/partial-work/start/{}'",
            '"refs/autocode/partial-work/start/{}"',
        ):
            with self.subTest(form=form):
                rows = [
                    evidence.signatures(ref_failure_output("/base", form.format(char * 40)), Path("/base"))
                    for char in ("a", "b")
                ]
                self.assertEqual(*rows)
        for size, chars in ((39, ("a", "b")), (41, ("a", "b")), (40, ("A", "B"))):
            with self.subTest(size=size, chars=chars):
                rows = [
                    evidence.signatures(
                        ref_failure_output("/base", "refs/autocode/partial-work/start/" + char * size), Path("/base")
                    )
                    for char in chars
                ]
                self.assertEqual(*rows)

    def test_ac1_volatile_message_text_keeps_the_same_signature(self):
        for number, (code, head_text, base_text) in enumerate(VOLATILE_PAIRS, 1):
            with self.subTest(pair=number):
                head = evidence.signatures(assertion_output("/head", code, head_text, line=17), Path("/head"))
                base = evidence.signatures(assertion_output("/base", code, base_text, line=12), Path("/base"))
                self.assertEqual(1, len(head))
                self.assertEqual("tests.test_runs.RunTests.test_status", head[0]["test"])
                self.assertEqual("FAIL", head[0]["kind"])
                self.assertEqual(head, base)

    def test_ac3_different_assertions_keep_different_signatures(self):
        original = assertion_output("/head", STRING_CODE, STRING_DIFF + TEMP_HEAD)
        other_value = assertion_output(
            "/head",
            STRING_CODE,
            "AssertionError: 'COMPLETED' != 'PAUSED_INTERVENTION'\n- COMPLETED\n+ PAUSED_INTERVENTION\n" + TEMP_HEAD,
        )
        other_exception = assertion_output(
            "/head",
            STRING_CODE,
            STRING_DIFF.replace("AssertionError:", "RuntimeError:") + TEMP_HEAD,
            kind="ERROR",
        )
        checker = unittest.TestCase()

        def printed(first, second):
            try:
                checker.assertEqual(first, second)
            except AssertionError as error:
                return "AssertionError: " + str(error)
            raise AssertionError("the fixture assertion passed")

        tuples = [
            assertion_output(
                "/head", "self.assertEqual(state, expected)", printed((status, 1), ("status : RUNNING", 1))
            )
            for status in ("status : PAUSED", "status : COMPLETED")
        ]
        self.assertTrue(
            printed(("status : PAUSED", 1), ("status : RUNNING", 1)).startswith(
                "AssertionError: Tuples differ: ('status : PAUSED', 1) != ('status : RUNNING', 1)"
            )
        )
        membership = [
            assertion_output(
                "/head", "self.assertIn(part, text, name)", f"AssertionError: '{part}' not found in 'xyz'" + RUN_HEAD
            )
            for part in ("a : b", "a : c")
        ]
        for label, first, second in (
            ("compared value", original, other_value),
            ("exception", original, other_exception),
            ("quoted tuple values", *tuples),
            ("quoted membership values", *membership),
            (
                "list values",
                assertion_output("/head", LIST_CODE, LIST_DIFF + TEMP_HEAD),
                assertion_output("/head", LIST_CODE, LIST_DIFF.replace("'zzz'", "'yyy'") + TEMP_HEAD),
            ),
            (
                "set values",
                assertion_output("/head", SET_CODE, SET_DIFF + ID_HEAD),
                assertion_output("/head", SET_CODE, SET_DIFF.removesuffix("2") + "3" + ID_HEAD),
            ),
        ):
            with self.subTest(label=label):
                self.assertNotEqual(
                    evidence.signatures(first, Path("/head")), evidence.signatures(second, Path("/head"))
                )
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            (base / "tests").mkdir()
            (base / "tests" / "test_runs.py").write_text("# fixture exists\n")
            head = evidence.signatures(original, Path("/head"))
            probe = type(
                "Probe", (), {"returncode": 1, "stdout": "", "stderr": other_value.replace("/head", str(base))}
            )()
            with mock.patch.object(attribute_failure.subprocess, "run", return_value=probe):
                self.assertEqual(
                    attribute_failure.DIFFERENT,
                    attribute_failure.classify("tests.test_runs", base, 60, head_failures=head),
                )

    def test_generated_ref_normalization_keeps_other_failure_details(self):
        original = ref_failure_output("/base", "refs/autocode/partial-work/start/" + "a" * 40)
        other_commit = ref_failure_output("/base", "refs/autocode/partial-work/start/" + "b" * 40)
        same = evidence.signatures(original, Path("/base"))
        for label, changed in (
            ("assertion", other_commit.replace("False is not true", "Expected an approved checkpoint")),
            ("exception", other_commit.replace("AssertionError:", "RuntimeError:")),
            ("frame", other_commit.replace("self.assertTrue(allowed, ref)", "self.assertTrue(verified, ref)")),
            ("test", ref_failure_output("/base", "refs/autocode/partial-work/start/" + "b" * 40, method="test_other")),
        ):
            with self.subTest(label=label):
                self.assertNotEqual(same, evidence.signatures(changed, Path("/base")))
        messages = [
            ref_failure_output("/base", "refs/autocode/partial-work/start/" + char * 40).replace(
                "AssertionError:", "AssertionError: source revision " + char * 40
            )
            for char in ("a", "b")
        ]
        self.assertNotEqual(*(evidence.signatures(output, Path("/base")) for output in messages))

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
