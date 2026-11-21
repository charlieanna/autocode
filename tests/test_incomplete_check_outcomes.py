"""A check that could not establish a result is never judged as a product or test defect (#3889, #3895).

The preservation judgment and the regression judgment read receipts the tests write here; pytest results are
parsed from JUnit XML the tests write. Nothing runs a command or calls a model.
"""

import copy
import tempfile
import unittest
from pathlib import Path

import autocode_check_outcome as check_outcome
import autocode_test_framework as test_framework
import autocode_test_results as test_results
import autocode_verify_judgment as judgment

PYTEST = test_framework.Framework("pytest", "python3 -m pytest")

COLLECTION_ABORT_XML = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" errors="1" failures="0" skipped="0" tests="1">
<testcase classname="pytest" name="internal" time="0.000"><error message="internal error">INTERNALERROR&gt; ValueError: bad marker in pytest_collection_modifyitems</error></testcase>
</testsuite></testsuites>
"""

ABORT_AFTER_TEST_XML = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" errors="1" failures="1" skipped="0" tests="3">
<testcase classname="tests.test_a" name="test_one" time="0.001"/>
<testcase classname="tests.test_a" name="test_two" time="0.001"><failure message="assert 1 == 2">assert 1 == 2</failure></testcase>
<testcase classname="pytest" name="internal" time="0.000"><error message="internal error">INTERNALERROR&gt; RuntimeError: plugin crashed</error></testcase>
</testsuite></testsuites>
"""

EMPTY_XML = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" errors="0" failures="0" skipped="0" tests="0"/></testsuites>
"""


def passing_base():
    return {
        "health": "passing",
        "receipt": {
            "exit_code": 0,
            "timed_out": False,
            "duration_seconds": 40,
            "results_expected": True,
            "results": {
                "passed": ["tests/test_a.py::test_one", "tests/test_a.py::test_two"],
                "failed": [],
                "skipped": [],
                "collection_errors": [],
                "uncollected": [],
                "total": 2,
                "complete": True,
            },
        },
    }


class IncompleteCheckOutcomeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def parse(self, xml, receipt):
        path = self.root / f"results-{len(list(self.root.iterdir()))}.xml"
        path.write_text(xml)
        return test_results.per_test_results(PYTEST, receipt, path)

    def judge_suite(self, candidate, base_suite):
        fail, unverified, notes = [], [], []
        judgment.judge_suite(candidate, base_suite, fail, unverified, notes)
        return fail, unverified, notes

    def test_ac2_two_timed_out_suites_leave_preservation_unverified(self):
        candidate = {
            "exit_code": None,
            "timed_out": True,
            "duration_seconds": 600,
            "results": None,
            "results_expected": True,
        }
        base_suite = {
            "health": "timeout",
            "receipt": {
                "exit_code": None,
                "timed_out": True,
                "duration_seconds": 590,
                "results": None,
                "results_expected": True,
            },
        }
        fail, unverified, _notes = self.judge_suite(candidate, base_suite)
        self.assertEqual([], fail)
        self.assertNotIn("The project suite timed out on the candidate.", fail)
        timed = [entry for entry in unverified if "timed out" in entry and "600" in entry and "590" in entry]
        self.assertEqual(1, len(timed), unverified)
        # The proof's rule: FAIL if fail, else UNVERIFIED if unverified, else PASS. It never passes.
        self.assertEqual("UNVERIFIED", "FAIL" if fail else "UNVERIFIED" if unverified else "PASS")

    def test_ac3_timed_out_suite_with_observed_regression_still_fails(self):
        candidate = {
            "exit_code": None,
            "timed_out": True,
            "duration_seconds": 600,
            "results_expected": True,
            "results": {
                "passed": ["tests/test_a.py::test_one"],
                "failed": ["tests/test_a.py::test_two"],
                "skipped": [],
                "collection_errors": [],
                "uncollected": [],
                "total": 2,
                "complete": False,
            },
        }
        fail, unverified, _notes = self.judge_suite(candidate, passing_base())
        self.assertEqual(["Tests that pass on base fail on the candidate: tests/test_a.py::test_two"], fail)
        self.assertTrue(any("timed out" in entry for entry in unverified), unverified)

    def test_ac15_candidate_only_timeout_with_passing_base_is_unverified(self):
        candidate = {
            "exit_code": None,
            "timed_out": True,
            "duration_seconds": 600,
            "results": None,
            "results_expected": True,
        }
        fail, unverified, _notes = self.judge_suite(candidate, passing_base())
        self.assertEqual([], fail)
        self.assertNotIn("The project suite timed out on the candidate", fail)
        self.assertNotIn("The project suite passes on base but fails on the candidate", fail)
        self.assertTrue(any("timed out" in entry and "600" in entry for entry in unverified), unverified)
        self.assertEqual("UNVERIFIED", "FAIL" if fail else "UNVERIFIED" if unverified else "PASS")

    def test_ac6_pytest_collection_time_abort_is_incomplete(self):
        receipt = {
            "exit_code": 3,
            "timed_out": False,
            "tail": "INTERNALERROR> ValueError: bad marker in pytest_collection_modifyitems",
        }
        results = self.parse(COLLECTION_ABORT_XML, receipt)
        self.assertIs(False, results["complete"])
        self.assertEqual([], results["passed"])
        self.assertEqual([], results["failed"])
        self.assertEqual([], results["skipped"])
        self.assertEqual(0, results["total"])
        for key in ("passed", "failed", "collection_errors", "uncollected"):
            self.assertNotIn("pytest::internal", results.get(key) or [], key)
        self.assertEqual(3, results["aborted"]["exit_code"])
        self.assertIn("ValueError: bad marker", results["aborted"]["diagnostic"])

    def test_ac7_pytest_abort_after_finished_test_keeps_genuine_outcomes(self):
        receipt = {"exit_code": 3, "timed_out": False, "tail": "INTERNALERROR> RuntimeError: plugin crashed"}
        results = self.parse(ABORT_AFTER_TEST_XML, receipt)
        self.assertEqual(["tests.test_a::test_one"], results["passed"])
        self.assertEqual(["tests.test_a::test_two"], results["failed"])
        self.assertEqual(2, results["total"])
        self.assertIs(False, results["complete"])
        self.assertEqual(3, results["aborted"]["exit_code"])
        self.assertIn("RuntimeError: plugin crashed", results["aborted"]["diagnostic"])

    def test_ac8_pytest_usage_error_exit_4_is_not_an_aborted_session(self):
        log = self.root / "pytest.log"
        log.write_text("collecting ...\nERROR: file or directory not found: tests/test_missing.py\n")
        receipt = {
            "exit_code": 4,
            "timed_out": False,
            "output": str(log),
            "tail": "ERROR: file or directory not found: tests/test_missing.py",
        }
        results = self.parse(EMPTY_XML, receipt)
        # A usage error ran no test: it is not an aborted session. A base where a new test file or
        # component does not exist yet reports exactly this, and has no passing test to preserve.
        self.assertEqual(0, results["total"])
        self.assertNotIn("aborted", results)

    def test_a_started_runner_that_cannot_load_a_test_module_failed(self):
        """unittest exits 1 on an import-time error and names no results file: its test is defective, and
        the existing test-correction path follows (#3877). Only a runner told to write a results file that
        wrote none did not start."""
        load_failure = {
            "exit_code": 1,
            "timed_out": False,
            "results": None,
            "results_expected": True,
            "tail": "unittest.case.SkipTest: product disabled",
        }
        self.assertEqual(check_outcome.FAILED, check_outcome.classify(load_failure)["outcome"])
        wrote = {**load_failure, "results_file_written": True}
        self.assertEqual(check_outcome.FAILED, check_outcome.classify(wrote)["outcome"])
        none = {**load_failure, "results_file_written": False}
        self.assertEqual(check_outcome.RUNNER_NOT_STARTED, check_outcome.classify(none)["reason"])

    def test_a_real_runner_that_cannot_start_writes_no_results_file(self):
        """The receipt records whether the results file the command was told to write exists, so a
        pytest that cannot be imported reads as not started, and one that started does not."""
        import subprocess
        import sys

        import autocode_verify as verify

        (self.root / "tests").mkdir()
        (self.root / "tests" / "test_a.py").write_text("def test_one():\n    assert True\n")
        bare = self.root / "bare"
        subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(bare)], check=True)
        cases = [(str(bare / "bin" / "python"), check_outcome.UNDETERMINED, False)]
        if subprocess.run([sys.executable, "-c", "import pytest"], capture_output=True).returncode == 0:
            cases.append((sys.executable, check_outcome.PASSED, True))
        for python, outcome, written in cases:
            with self.subTest(python=python):
                command = f"{python} -m pytest -q tests"
                evidence = self.root / ("evidence-" + str(written))
                evidence.mkdir()
                receipt = verify.run_suite(
                    verify.Framework("pytest", command), command, self.root, evidence, "suite", timeout=120
                )
                self.assertIs(written, receipt["results_file_written"])
                self.assertEqual(outcome, check_outcome.classify(receipt)["outcome"])

    def test_pytest_result_capture_is_bound_to_the_runner_receipt(self):
        import subprocess
        import sys

        import autocode_verification_schedule as schedule
        import autocode_verify as verify

        if subprocess.run([sys.executable, "-c", "import pytest"], capture_output=True).returncode:
            self.skipTest("pytest is not installed")
        (self.root / "tests").mkdir()
        (self.root / "tests" / "test_a.py").write_text("def test_one():\n    assert False, 'failure detail'\n")
        command = f"{sys.executable} -m pytest -q tests"
        evidence = self.root / "evidence"
        evidence.mkdir()
        receipt = verify.run_suite(
            verify.Framework("pytest", command), command, self.root, evidence, "suite", timeout=120
        )
        self.assertTrue(receipt["results_file_written"])
        self.assertFalse(
            schedule.results_capture_intact({key: value for key, value in receipt.items() if key != "results_capture"})
        )
        self.assertTrue(schedule.intact(receipt, root=evidence))
        result_file = Path(receipt["results_capture"]["path"])
        self.assertTrue(result_file.is_file())
        result_file.write_text("<testsuites/>")
        self.assertFalse(schedule.intact(receipt, root=evidence))
        result_file.unlink()
        self.assertFalse(schedule.intact(receipt, root=evidence))
        result_file.symlink_to(Path(receipt["output"]))
        self.assertFalse(schedule.intact(receipt, root=evidence))

    def test_unreadable_result_capture_does_not_crash_or_authorize_proof(self):
        import subprocess
        import sys
        from unittest.mock import patch

        import autocode_verification_schedule as schedule
        import autocode_verify as verify

        if subprocess.run([sys.executable, "-c", "import pytest"], capture_output=True).returncode:
            self.skipTest("pytest is not installed")
        (self.root / "test_a.py").write_text("def test_one():\n    assert True\n")
        evidence = self.root / "evidence"
        evidence.mkdir()
        command = f"{sys.executable} -m pytest -q test_a.py"
        file_hash = verify.util.file_hash

        def hash_or_fail(path):
            if str(path).endswith(".junit.xml"):
                raise OSError("result file became unreadable")
            return file_hash(path)

        with patch.object(verify.util, "file_hash", side_effect=hash_or_fail):
            receipt = verify.run_suite(
                verify.Framework("pytest", command), command, self.root, evidence, "suite", timeout=120
            )
        self.assertFalse(schedule.intact(receipt, root=evidence))

    def test_ac9_aborted_base_reports_incomplete_preservation_evidence(self):
        aborted = {"exit_code": 3, "timed_out": False, "tail": "INTERNALERROR> RuntimeError: plugin crashed"}
        base_suite = {
            "health": "broken",
            "receipt": {
                "exit_code": 3,
                "timed_out": False,
                "results_expected": True,
                "results": self.parse(ABORT_AFTER_TEST_XML, copy.deepcopy(aborted)),
            },
        }
        candidate = {
            "exit_code": 1,
            "timed_out": False,
            "results_expected": True,
            "results": {
                "passed": ["tests.test_a::test_one"],
                "failed": ["tests.test_a::test_two"],
                "skipped": [],
                "collection_errors": [],
                "uncollected": [],
                "total": 2,
                "complete": True,
            },
        }
        fail, unverified, notes = self.judge_suite(candidate, base_suite)
        self.assertEqual([], fail)
        self.assertFalse(any("already failed on base; none newly fail" in note for note in notes), notes)
        self.assertTrue(any("aborted" in entry and "exit 3" in entry for entry in unverified), unverified)

    def test_ac10_aborted_base_grants_no_fail_to_pass(self):
        aborted = {
            "exit_code": 3,
            "timed_out": False,
            "tail": "INTERNALERROR> ValueError: bad marker in pytest_collection_modifyitems",
        }
        base = {
            "exit_code": 3,
            "timed_out": False,
            "results_expected": True,
            "results": self.parse(COLLECTION_ABORT_XML, copy.deepcopy(aborted)),
        }
        candidate = {
            "exit_code": 0,
            "timed_out": False,
            "results_expected": True,
            "results": {
                "passed": ["tests.test_a::test_one"],
                "failed": [],
                "skipped": [],
                "collection_errors": [],
                "uncollected": [],
                "total": 1,
                "complete": True,
            },
        }
        fail, unverified, notes, proof, review_reasons = [], [], [], {}, []
        judgment.judge_regression(
            candidate, base, fail, unverified, notes, proof, review_reasons, known_failures=lambda: set()
        )
        self.assertEqual([], fail)
        self.assertNotIn("fail_to_pass", proof)
        self.assertIn("Per-test results on the base code were incomplete", unverified)

    def test_a_timed_out_candidate_names_its_timing_and_what_was_observed(self):
        candidate = {
            "exit_code": None,
            "timed_out": True,
            "duration_seconds": 600.5,
            "results": None,
            "results_expected": True,
        }
        fail, unverified, _notes = self.judge_suite(candidate, passing_base())
        self.assertEqual([], fail)
        self.assertEqual(
            [
                "The project suite timed out after 600.5 s on the candidate, so it could not establish a result; "
                "no failure of a test that passes on base was observed; preservation is unproven"
            ],
            unverified,
        )
        recorded = {
            **candidate,
            "duration_seconds": 600,
            "results": {
                "passed": [],
                "failed": ["tests/test_a.py::test_two"],
                "skipped": [],
                "collection_errors": [],
                "uncollected": [],
                "total": 1,
                "complete": False,
            },
        }
        fail, unverified, _notes = self.judge_suite(recorded, passing_base())
        self.assertEqual(["Tests that pass on base fail on the candidate: tests/test_a.py::test_two"], fail)
        self.assertEqual(
            [
                "The project suite timed out after 600 s on the candidate, so it could not establish a result; "
                "preservation is unproven"
            ],
            unverified,
        )

    def test_a_timed_out_suite_without_a_recorded_duration_names_no_timing(self):
        # A timeout receipt may carry no duration, or a value that is not a number of seconds.
        for name, duration in (
            ("missing", {}),
            ("none", {"duration_seconds": None}),
            ("bool", {"duration_seconds": True}),
        ):
            with self.subTest(name):
                candidate = {
                    "exit_code": None,
                    "timed_out": True,
                    "results": None,
                    "results_expected": True,
                    **duration,
                }
                base_suite = {"health": "timeout", "receipt": copy.deepcopy(candidate)}
                fail, unverified, _notes = self.judge_suite(candidate, base_suite)
                self.assertEqual([], fail)
                self.assertEqual(
                    [
                        "The project suite timed out on the candidate and timed out on base, so it could not "
                        "establish a result; no failure of a test that passes on base was observed; "
                        "preservation is unproven"
                    ],
                    unverified,
                )

    def test_an_aborted_base_names_its_diagnostic(self):
        aborted = {"exit_code": 3, "timed_out": False, "tail": "INTERNALERROR> RuntimeError: plugin crashed"}
        base_suite = {
            "health": "broken",
            "receipt": {
                "exit_code": 3,
                "timed_out": False,
                "results_expected": True,
                "results": self.parse(ABORT_AFTER_TEST_XML, aborted),
            },
        }
        candidate = copy.deepcopy(base_suite["receipt"])
        candidate.update(exit_code=1, results={**candidate["results"], "complete": True})
        candidate["results"].pop("aborted")
        _fail, unverified, _notes = self.judge_suite(candidate, base_suite)
        self.assertIn(
            "The base suite aborted its test session (exit 3): RuntimeError: plugin crashed; preservation evidence "
            "is incomplete",
            unverified,
        )

    def test_a_pytest_abort_diagnostic_comes_from_the_internal_error_then_the_tail_then_the_log(self):
        internal_error = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" errors="1" failures="0" skipped="0" tests="1">
<testcase classname="pytest" name="internal" time="0.000"><error message="internal error">INTERNALERROR&gt; KeyError: plugin state</error></testcase>
</testsuite></testsuites>
"""
        bare = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" errors="1" failures="0" skipped="0" tests="1">
<testcase classname="pytest" name="internal" time="0.000"/>
</testsuite></testsuites>
"""
        log = self.root / "aborted.log"
        log.write_text("collecting ...\nINTERNALERROR> OSError: disk full\n")
        for name, xml, receipt, diagnostic in (
            ("internal error", internal_error, {"exit_code": 3, "tail": "pytest exited"}, "KeyError: plugin state"),
            ("tail", bare, {"exit_code": 3, "tail": "INTERNALERROR> TypeError: bad hook"}, "TypeError: bad hook"),
            ("log", bare, {"exit_code": 3, "tail": "", "output": str(log)}, "OSError: disk full"),
        ):
            with self.subTest(name):
                results = self.parse(xml, {"timed_out": False, **receipt})
                self.assertEqual({"exit_code": 3, "diagnostic": diagnostic}, results["aborted"])
                self.assertIs(False, results["complete"])
                self.assertEqual(0, results["total"])

    def test_a_completed_base_failure_that_still_fails_keeps_the_already_failed_note(self):
        results = {
            "passed": ["tests.test_a::test_one"],
            "failed": ["tests.test_a::test_two"],
            "skipped": [],
            "collection_errors": [],
            "uncollected": [],
            "total": 2,
            "complete": True,
        }
        receipt = {"exit_code": 1, "timed_out": False, "results_expected": True, "results": results}
        base_suite = {"health": "broken", "receipt": copy.deepcopy(receipt)}
        fail, _unverified, notes = self.judge_suite(copy.deepcopy(receipt), base_suite)
        self.assertEqual([], fail)
        self.assertIn("1 test(s) already failed on base; none newly fail", notes)

    def test_a_completed_passing_base_and_candidate_leave_preservation_proven(self):
        # A completed base names no abort, so its classification adds no incomplete-evidence entry.
        candidate = copy.deepcopy(passing_base()["receipt"])
        fail, unverified, _notes = self.judge_suite(candidate, passing_base())
        self.assertEqual([], fail)
        self.assertEqual([], unverified)

    def test_the_classifier_names_each_outcome_and_its_reason(self):
        # Imported here, not at the top of the file: the classifier is added by this change, and the other
        # cases of this file run unchanged on the code before it.
        import autocode_check_outcome as check_outcome

        completed = copy.deepcopy(passing_base()["receipt"])
        failed = copy.deepcopy(completed)
        failed.update(exit_code=1)
        failed["results"].update(passed=["tests/test_a.py::test_one"], failed=["tests/test_a.py::test_two"])
        timed_out = {**failed, "exit_code": None, "timed_out": True, "duration_seconds": 600}
        aborted = copy.deepcopy(failed)
        aborted["results"].update(complete=False, aborted={"exit_code": 3, "diagnostic": "RuntimeError: crashed"})
        not_started = {
            "exit_code": 1,
            "timed_out": False,
            "results": None,
            "results_expected": True,
            "results_file_written": False,
            "tail": "Traceback\n/usr/bin/python3: No module named pytest\n",
        }
        unsupervised = {
            **completed,
            "supervision": {"receipt": "/missing/supervision.json"},
            "supervision_sha256": "0" * 64,
            "supervision_errors": ["keeper exited before collection"],
        }
        two = ["tests/test_a.py::test_two"]
        for name, receipt, expected in (
            ("passed", completed, {"outcome": "passed", "reason": "", "detail": "", "failed": []}),
            ("failed", failed, {"outcome": "failed", "reason": "", "detail": "", "failed": two}),
            (
                "nonzero exit",
                {**completed, "exit_code": 2},
                {"outcome": "failed", "reason": "", "detail": "", "failed": []},
            ),
            (
                "legacy error",
                {**completed, "error": "no tests were reported"},
                {"outcome": "failed", "reason": "", "detail": "", "failed": []},
            ),
            (
                "timeout",
                timed_out,
                {"outcome": "undetermined", "reason": "timeout", "detail": "timed out after 600 s", "failed": two},
            ),
            (
                "incomplete receipt",
                unsupervised,
                {
                    "outcome": "undetermined",
                    "reason": "incomplete_receipt",
                    "detail": "supervision receipt is incomplete: keeper exited before collection",
                    "failed": [],
                },
            ),
            (
                "missing receipt",
                None,
                {
                    "outcome": "undetermined",
                    "reason": "incomplete_receipt",
                    "detail": "supervision receipt is missing: no run was recorded",
                    "failed": [],
                },
            ),
            (
                "session aborted",
                aborted,
                {
                    "outcome": "undetermined",
                    "reason": "session_aborted",
                    "detail": "aborted its test session (exit 3): RuntimeError: crashed",
                    "failed": two,
                },
            ),
            (
                "runner not started",
                not_started,
                {
                    "outcome": "undetermined",
                    "reason": "runner_not_started",
                    "detail": "could not start its test runner (exit 1): /usr/bin/python3: No module named pytest",
                    "failed": [],
                },
            ),
            (
                "merged, all parts pass",
                {"suites": [completed, completed]},
                {"outcome": "passed", "reason": "", "detail": "", "failed": []},
            ),
            (
                "merged, a failed part wins",
                {"suites": [timed_out, failed]},
                {"outcome": "failed", "reason": "", "detail": "", "failed": two},
            ),
            (
                "merged, an undetermined part names itself",
                {"suites": [completed, timed_out]},
                {
                    "outcome": "undetermined",
                    "reason": "timeout",
                    "detail": "timed out after 600 s (in part 2 of 2)",
                    "failed": two,
                },
            ),
        ):
            with self.subTest(name):
                self.assertEqual(expected, check_outcome.classify(copy.deepcopy(receipt)))
        self.assertEqual(
            "the original half's supervision receipt is incomplete: keeper exited before collection",
            check_outcome.describe("the original half", check_outcome.classify(unsupervised)),
        )
        self.assertEqual(
            "the candidate half timed out after 600 s",
            check_outcome.describe("the candidate half", check_outcome.classify(timed_out)),
        )
        self.assertEqual(
            "the base could not establish a result", check_outcome.describe("the base", {"reason": "timeout"})
        )

    def test_a_completed_pytest_failure_is_not_an_abort(self):
        xml = ABORT_AFTER_TEST_XML.replace(
            '<testcase classname="pytest" name="internal" time="0.000"><error message="internal error">'
            "INTERNALERROR&gt; RuntimeError: plugin crashed</error></testcase>\n",
            "",
        )
        results = self.parse(xml, {"exit_code": 1, "timed_out": False, "tail": "1 failed, 1 passed"})
        self.assertNotIn("aborted", results)
        self.assertIs(True, results["complete"])
        self.assertEqual(["tests.test_a::test_two"], results["failed"])


if __name__ == "__main__":
    unittest.main()
