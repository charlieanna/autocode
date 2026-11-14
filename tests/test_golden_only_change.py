"""A change that rewrites only a file tests read (a golden file, a snapshot) is proven by the tests that read it.

A real run (Claude models, 2026-11-14) of "print the date as 2026-03-04; the golden file test already covers
the format, so just update tests/golden/report.txt; don't add or change any test code" changed ``report.py``
and the golden file. A golden file is a test file no runner can be pointed at, so the proof had no regression
command ("The proving tests are not in the suite that ran"), and the person was asked to "Set the regression
command to ``python3 -m unittest -v tests.test_report.ReportTests.test_report_matches_the_golden_file``".

These cases run real regression proofs and the real original-test gate in a Git project; no provider runs.
tools/autocode_verification_commands.py (held_by_suite), tools/autocode_superseded_tests.py (edited)
"""

from __future__ import annotations

import shutil
import unittest

import autocode_protected_commands as commands
import autocode_protected_oracles as guard
import autocode_superseded_correction as correction
import autocode_superseded_tests as superseded_tests
import autocode_verification_commands as verification_commands
import autocode_verify as verify
from autocode_test_framework import Framework

from . import test_changed_guard_correction as guards
from .test_verify import Project

GOLDEN, ENTRY = guards.GOLDEN, guards.ENTRY
TEST_ID = f"tests.test_report.ReportTests.{GOLDEN}"
# What the real run's Builder delivered: the code and the golden file, no test code.
CHANGE = {name: text for name, text in guards.CHANGE.items() if name != "tests/test_report.py"}
PLAN = [
    {"id": "AC1", "criterion": "the report equals the golden file", "verification_method": f"guard: {GOLDEN}"},
    {"id": "AC2", "criterion": "no rows", "verification_method": "guard: test_no_rows_is_an_empty_report"},
]
GO = {
    "go.mod": "module example.com/report\n\ngo 1.21\n",
    "report.go": 'package report\n\nimport "fmt"\n\n'
    '// Line is one stock line.\nfunc Line(name string, count int) string {\n\treturn fmt.Sprintf("%s %d\\n", name, count)\n}\n',
    "report_test.go": 'package report\n\nimport (\n\t"os"\n\t"testing"\n)\n\n'
    'func TestLineMatchesGolden(t *testing.T) {\n\twant, err := os.ReadFile("testdata/line.golden")\n'
    '\tif err != nil {\n\t\tt.Fatal(err)\n\t}\n\tif got := Line("pen", 12); got != string(want) {\n'
    '\t\tt.Fatalf("got %q, want %q", got, want)\n\t}\n}\n\n'
    "func TestLineEndsWithANewline(t *testing.T) {\n\tif got := Line(\"ink\", 3); got[len(got)-1] != '\\n' {\n"
    '\t\tt.Fatalf("got %q", got)\n\t}\n}\n',
    "testdata/line.golden": "pen 12\n",
}


class CommandTests(unittest.TestCase):
    def test_only_files_tests_read_are_left_to_a_suite_that_names_its_tests(self):
        unittest_suite, make = Framework("unittest", "python -m unittest discover -v"), Framework("make", "make test")
        for framework, suite, paths, held in (
            (unittest_suite, unittest_suite.suite, ["tests/golden/report.txt"], True),
            (unittest_suite, unittest_suite.suite, ["src/__snapshots__/x.test.js.snap", "testdata/a.golden"], True),
            # A test file that holds code is one the suite must be shown to run.
            (unittest_suite, unittest_suite.suite, ["tests/golden/report.txt", "tests/helpers.py"], False),
            (unittest_suite, unittest_suite.suite, ["spec/report_spec.rb"], False),
            (unittest_suite, unittest_suite.suite, [], False),
            # A command that is not the framework's own suite, and a runner that names no test.
            (unittest_suite, "sh run-tests.sh", ["tests/golden/report.txt"], False),
            (make, make.suite, ["tests/golden/report.txt"], False),
            (None, "make test", ["tests/golden/report.txt"], False),
        ):
            with self.subTest(framework=framework and framework.name, suite=suite, paths=paths):
                self.assertEqual(held, verification_commands.held_by_suite(framework, suite, paths))

    def test_a_replaced_test_is_edited_through_its_own_file_or_a_test_file_that_holds_no_code(self):
        for changed, edited in (
            (["tests/test_report.py"], True),
            (["tests/golden/report.txt"], True),
            (["tests/helpers.py", "testdata/report.golden"], True),
            # Another test file that holds code, and nothing.
            (["tests/helpers.py"], False),
            (["tests/test_other.py", "spec/report_spec.rb"], False),
            ([], False),
        ):
            with self.subTest(changed=changed):
                self.assertEqual(edited, superseded_tests.edited(ENTRY, changed))

    def test_only_a_listed_test_the_change_edits_is_an_accepted_original_failure(self):
        results = {"complete": True, "failed": [TEST_ID], "passed": [], "total": 2}
        row = {"exit_code": 1, "results": results}
        self.assertEqual([TEST_ID], superseded_tests.excused(row, [ENTRY], ["tests/golden/report.txt"]))
        self.assertIsNone(superseded_tests.excused(row, [ENTRY], ["tests/test_other.py"]))
        self.assertIsNone(superseded_tests.excused(row, [], ["tests/golden/report.txt"]))
        other = {"exit_code": 1, "results": {**results, "failed": [TEST_ID, "tests.test_report.ReportTests.test_x"]}}
        self.assertIsNone(superseded_tests.excused(other, [ENTRY], ["tests/golden/report.txt"]))


class ProofTests(unittest.TestCase):
    def verify(self, seed, change):
        project = Project(seed)
        self.addCleanup(project.close)
        project.write(change)
        return project.verify()

    def test_the_test_that_reads_the_rewritten_golden_file_is_the_proof(self):
        result = self.verify(guards.SEED, CHANGE)
        self.assertEqual(verify.PASS, result["verdict"], result)
        self.assertEqual([TEST_ID], result["fail_to_pass"])
        self.assertEqual(["tests.test_report.ReportTests.test_no_rows_is_an_empty_report"], result["pass_to_pass"])
        self.assertEqual(result["commands"]["suite"], result["commands"]["regression"])
        self.assertEqual("suite:unittest", result["commands"]["regression_source"])

    def test_a_rewritten_file_no_test_reads_proves_nothing(self):
        seed = {**guards.SEED, "tests/golden/notes.txt": "old\n"}
        change = {"report.py": guards.REPORT + "\n# reviewed\n", "tests/golden/notes.txt": "new\n"}
        result = self.verify(seed, change)
        self.assertEqual(verify.FAIL, result["verdict"], result)
        self.assertIn("No test fails on the unfixed base code and passes with the fix", " ".join(result["failures"]))

    def test_a_golden_file_that_does_not_match_the_change_is_refused(self):
        result = self.verify(guards.SEED, {**CHANGE, "tests/golden/report.txt": "ann: 2026-3-4\n"})
        self.assertEqual(verify.FAIL, result["verdict"], result)

    def test_a_changed_test_file_beside_the_golden_file_is_targeted_as_before(self):
        result = self.verify(guards.SEED, guards.CHANGE)
        self.assertEqual(verify.PASS, result["verdict"], result)
        self.assertEqual("derived:unittest", result["commands"]["regression_source"])

    @unittest.skipUnless(shutil.which("go"), "Go is required for native suite proof")
    def test_a_go_testdata_file_is_proved_the_same_way(self):
        change = {
            "report.go": GO["report.go"].replace('"%s %d\\n"', '"%-8s%4d\\n"'),
            "testdata/line.golden": "pen       12\n",
        }
        result = self.verify(GO, change)
        self.assertEqual(verify.PASS, result["verdict"], result)
        self.assertEqual(["example.com/report::TestLineMatchesGolden"], result["fail_to_pass"])
        self.assertEqual("suite:go", result["commands"]["regression_source"])


class RunTests(guards.Run):
    def replay(self):
        return guard.replay(self.state, self.root, self.run_dir / "replays", verify.scratch_run, timeout=60)

    def test_the_guarded_golden_test_becomes_a_replaced_test_and_both_gates_pass(self):
        # The real run's plan and change: the proof now runs, refuses the guard alone, and the plan is corrected.
        self.approve(PLAN)
        proof = self.prove(CHANGE)
        self.assertEqual(verify.FAIL, proof["verdict"], proof)
        self.assertEqual([], proof["unverified"])
        self.assertEqual([{"id": "AC1", "tests": [TEST_ID]}], proof["changed_guards"])
        found = correction.guard_proposal(self.state)
        self.assertEqual([{"criterion": "AC1", **ENTRY}], found["tests"])
        self.approve(found["body"]["acceptance_criteria"], revision=2)
        proof = self.prove(CHANGE)
        self.assertEqual(verify.PASS, proof["verdict"], proof)
        self.assertEqual([TEST_ID], proof["case_tests"]["AC1"])
        # The original golden file is put back for the original tests, where the listed test is the one failure.
        result = self.replay()
        self.assertEqual("PASS", result["verdict"])
        self.assertEqual(["tests/golden/report.txt"], result["changed_tests"])
        self.assertEqual([TEST_ID], result["superseded"]["excused_failures"])
        self.assertEqual([{"criterion": "AC1", **ENTRY}], result["superseded"]["tests"])

    def test_an_unlisted_test_that_reads_the_rewritten_file_is_sent_back_to_plan_review(self):
        plan = [{"id": "AC1", "criterion": "padded", "verification_method": f"test: {GOLDEN}"}]
        self.approve(plan)
        self.project.write(CHANGE)
        with self.assertRaises(commands.OriginalGateFailure) as caught:
            self.replay()
        receipt = caught.exception.receipt
        self.assertEqual([TEST_ID], receipt["original"]["results"]["failed"])
        found = correction.proposal(self.state, receipt, self.root)
        self.assertEqual([{"criterion": "AC1", **ENTRY}], found["tests"])
        self.approve(found["body"]["acceptance_criteria"], revision=2)
        self.assertEqual("PASS", self.replay()["verdict"])


if __name__ == "__main__":
    unittest.main()
