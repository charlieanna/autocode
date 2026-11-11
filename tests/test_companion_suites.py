"""The other project suites a change reaches, and how their runs are judged (#2889)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import autocode_companion_suites as companions
import autocode_package_scope as package_scope
from autocode_test_framework import Framework

ROOT_NODE = Framework("node", "npm test --silent", invocation=None, node_files=("frontend/routing.test.mjs",))
RSPEC = Framework("rspec", "bundle exec rspec", runner="bundle exec rspec")


def receipt(exit_code, *, passed=None, timed_out=False):
    found = {"exit_code": exit_code, "timed_out": timed_out}
    return {**found, "results": {"passed": passed, "failed": []}} if passed is not None else found


class Repository:
    def __init__(self, case, files):
        temp = tempfile.TemporaryDirectory()
        case.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for path, text in files.items():
            (self.root / path).parent.mkdir(parents=True, exist_ok=True)
            (self.root / path).write_text(text)


POLYGLOT = {
    "package.json": "{}\n",
    "frontend/routing.mjs": "",
    "frontend/routing.test.mjs": "",
    "backend/Gemfile": "",
    "backend/app/grader.rb": "",
    "backend/spec/grader_spec.rb": "",
}
TESTS = ["backend/spec/grader_spec.rb", "frontend/routing.test.mjs"]


class FindTests(unittest.TestCase):
    def test_a_project_is_the_innermost_directory_with_its_own_manifest(self):
        repo = Repository(self, {**POLYGLOT, "backend/engines/billing/Gemfile": ""})
        self.assertEqual("backend", companions.project_of(repo.root, "backend/app/grader.rb"))
        self.assertEqual("backend/engines/billing", companions.project_of(repo.root, "backend/engines/billing/x.rb"))
        self.assertEqual(companions.ROOT, companions.project_of(repo.root, "frontend/routing.mjs"))
        self.assertEqual(companions.ROOT, companions.project_of(repo.root, "setup.py"))

    def test_code_in_a_project_the_proof_suite_cannot_run_gets_that_projects_suite(self):
        repo = Repository(self, POLYGLOT)
        detect = lambda directory: RSPEC if (directory / "Gemfile").is_file() else None  # noqa: E731
        suites, notes = companions.find(
            repo.root, ROOT_NODE, ROOT_NODE, ["backend/app/grader.rb", "frontend/routing.mjs"], TESTS, detect
        )
        self.assertEqual([("backend", "cd backend && bundle exec rspec")], [(at, f.suite) for at, f in suites])
        self.assertEqual(
            [
                "Code in backend/ changed and the proof's suite `npm test --silent` cannot run its tests, so its own "
                "suite `cd backend && bundle exec rspec` ran on the original code and with the change"
            ],
            notes,
        )

    def test_the_root_suite_checks_root_code_when_a_package_suite_proves_the_change(self):
        repo = Repository(self, POLYGLOT)
        backend = package_scope.PackageFramework(RSPEC, "backend")
        suites, _ = companions.find(repo.root, backend, ROOT_NODE, ["frontend/routing.mjs"], TESTS, lambda d: None)
        self.assertEqual([(companions.ROOT, ROOT_NODE)], suites)
        self.assertEqual(".", companions.label(companions.ROOT))
        # The proof's own project never gets a second suite.
        self.assertEqual(
            ([], []), companions.find(repo.root, backend, ROOT_NODE, ["backend/app/grader.rb"], TESTS, lambda d: None)
        )

    def test_no_second_suite_for_tests_the_proof_suite_may_run_or_for_a_project_without_tests(self):
        repo = Repository(self, {**POLYGLOT, "tools/package.json": "{}\n", "tools/build.mjs": ""})
        detect = lambda directory: RSPEC  # noqa: E731
        node_everywhere = Framework("node", "npm test", node_files=("backend/spec/grader_spec.rb",))
        make = Framework("make", "make test")
        for framework in (node_everywhere, make):
            with self.subTest(suite=framework.suite):
                self.assertEqual(
                    ([], []),
                    companions.find(repo.root, framework, framework, ["backend/app/grader.rb"], TESTS, detect),
                )
        self.assertEqual(([], []), companions.find(repo.root, ROOT_NODE, ROOT_NODE, ["tools/build.mjs"], TESTS, detect))
        self.assertEqual(([], []), companions.find(repo.root, None, None, ["backend/app/grader.rb"], TESTS, detect))

    def test_a_project_without_a_detected_suite_is_noted(self):
        repo = Repository(self, POLYGLOT)
        suites, notes = companions.find(
            repo.root, ROOT_NODE, ROOT_NODE, ["backend/app/grader.rb"], TESTS, lambda directory: None
        )
        self.assertEqual([], suites)
        self.assertEqual(
            [
                "Code in backend/ changed and the proof's suite `npm test --silent` cannot run its tests, but no suite "
                "was found for it, so its existing tests were not run"
            ],
            notes,
        )


class JudgeTests(unittest.TestCase):
    def test_a_test_that_passed_and_no_longer_passes_fails_the_proof(self):
        failures, unverified = companions.judge(
            "backend", "cd backend && rspec", receipt(1, passed=["a", "b"]), receipt(1, passed=["a"])
        )
        self.assertEqual(
            (
                [
                    "The backend/ suite `cd backend && rspec`: tests that pass on the original code do not pass "
                    "with the change: b"
                ],
                [],
            ),
            (failures, unverified),
        )
        many = [f"t{n:02d}" for n in range(12)]
        [failure], _ = companions.judge(companions.ROOT, "npm test", receipt(0, passed=many), receipt(1, passed=[]))
        self.assertTrue(failure.startswith("The repository root's suite `npm test`: "), failure)
        self.assertTrue(failure.endswith(": t00, t01, t02, t03, t04, t05, t06, t07, t08, t09 and 2 more"), failure)

    def test_per_test_results_judge_a_suite_that_already_fails_on_the_original_code(self):
        self.assertEqual(
            ([], []), companions.judge("backend", "rspec", receipt(1, passed=["a"]), receipt(1, passed=["a", "c"]))
        )

    def test_exit_codes_judge_a_suite_without_per_test_results(self):
        self.assertEqual(([], []), companions.judge("backend", "rspec", receipt(0), receipt(0)))
        self.assertEqual(
            (["The backend/ suite `rspec` passes on the original code and fails with the change (exit 1)"], []),
            companions.judge("backend", "rspec", receipt(0), receipt(1)),
        )
        self.assertEqual(
            (
                [],
                [
                    "The backend/ suite `rspec` fails on the original code (exit 2) and reports no per-test results, "
                    "so it cannot show whether the change breaks a test there"
                ],
            ),
            companions.judge("backend", "rspec", receipt(2), receipt(0)),
        )

    def test_a_run_that_did_not_finish_shows_nothing(self):
        for on_base, on_change, when in (
            (receipt(0, timed_out=True), receipt(0), "on the original code"),
            (receipt(0), receipt(None, timed_out=True), "with the change"),
            (receipt(0), {**receipt(1), "supervision": None}, "with the change"),
        ):
            with self.subTest(when=when):
                self.assertEqual(
                    (
                        [],
                        [
                            f"The backend/ suite `rspec` did not finish {when}, so it cannot show whether the change "
                            "breaks a test there"
                        ],
                    ),
                    companions.judge("backend", "rspec", on_base, on_change),
                )

    def test_findings_only_lower_the_verdict(self):
        def verdict(current, failures, unverified):
            return companions.verdict(current, failures, unverified, fail="FAIL", unverified_verdict="UNVERIFIED")

        self.assertEqual("PASS", verdict("PASS", [], []))
        self.assertEqual("FAIL", verdict("PASS", ["x"], []))
        self.assertEqual("FAIL", verdict("UNVERIFIED", ["x"], []))
        self.assertEqual("UNVERIFIED", verdict("PASS", [], ["y"]))
        self.assertEqual("FAIL", verdict("FAIL", [], ["y"]))
        self.assertEqual("UNVERIFIED", verdict("UNVERIFIED", [], []))


if __name__ == "__main__":
    unittest.main()
