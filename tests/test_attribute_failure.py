"""A red suite names whose failure it is (#2603)."""

import tempfile
import unittest
from pathlib import Path

import attribute_failure
import run_suite


class FirstSkipTests(unittest.TestCase):
    def test_the_first_skip_reason_is_named_and_absent_when_nothing_skipped(self):
        self.assertEqual("needs Linux", run_suite.first_skip("... skipped 'needs Linux' ... skipped 'other'"))
        self.assertEqual("", run_suite.first_skip("Ran 3 tests\n\nOK"))
        self.assertEqual("", run_suite.first_skip(""))


class ReportTests(unittest.TestCase):
    def test_the_table_says_which_modules_are_this_branchs_to_fix(self):
        text = attribute_failure.report([("tests.a", "pre-existing"), ("tests.b", "introduced")])
        self.assertIn("| `tests.a` | fails | pre-existing |", text)
        self.assertIn("| `tests.b` | passes | introduced |", text)
        self.assertIn("1 introduced", text)
        self.assertIn("`tests.b`", text)

    def test_all_pre_existing_reads_as_nothing_to_fix(self):
        text = attribute_failure.report([("tests.a", "pre-existing")])
        self.assertIn("Nothing here is this branch's to fix", text)


class ClassifyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        (self.base / "tests").mkdir()

    def module(self, name, body):
        (self.base / "tests" / f"{name}.py").write_text(body)

    def test_a_module_the_base_does_not_have_cannot_be_the_bases_failure(self):
        self.assertEqual(attribute_failure.NEW_MODULE, attribute_failure.classify("tests.absent", self.base, 60))

    def test_a_module_that_fails_on_the_base_is_pre_existing(self):
        self.module(
            "broken",
            "import unittest\nclass T(unittest.TestCase):\n    def test_x(self):\n        self.fail('already red')\n",
        )
        self.assertEqual(attribute_failure.PRE_EXISTING, attribute_failure.classify("tests.broken", self.base, 60))

    def test_a_module_that_passes_on_the_base_was_introduced_here(self):
        self.module("fine", "import unittest\nclass T(unittest.TestCase):\n    def test_x(self):\n        pass\n")
        self.assertEqual(attribute_failure.INTRODUCED, attribute_failure.classify("tests.fine", self.base, 60))

    def test_the_module_sees_the_base_checkout_not_this_branch(self):
        # The verdict must come from the base's own copy: the same module name with a
        # different result there is what makes the two verdicts mean anything.
        self.module(
            "moved", "import unittest\nclass T(unittest.TestCase):\n    def test_x(self):\n        self.fail()\n"
        )
        self.assertEqual(attribute_failure.PRE_EXISTING, attribute_failure.classify("tests.moved", self.base, 60))
        (self.base / "tests" / "moved.py").write_text(
            "import unittest\nclass T(unittest.TestCase):\n    def test_x(self):\n        pass\n"
        )
        self.assertEqual(attribute_failure.INTRODUCED, attribute_failure.classify("tests.moved", self.base, 60))


class FailedOutTests(unittest.TestCase):
    def test_the_failed_module_list_is_written_for_the_attribution_pass(self):
        import json
        from unittest import mock

        with tempfile.TemporaryDirectory() as scratch:
            out = Path(scratch) / "failed.json"
            red = {"module": "tests.red", "ok": False, "tests": 1, "seconds": 0.0, "output": "FAIL"}
            green = {"module": "tests.green", "ok": True, "tests": 1, "seconds": 0.0, "output": "OK"}
            with mock.patch.object(run_suite, "run_module", side_effect=[red, green]):
                run_suite.run_parallel(["tests.red", "tests.green"], 1, 0, failed_out=out)
            self.assertEqual(["tests.red"], json.loads(out.read_text()))


if __name__ == "__main__":
    unittest.main()
