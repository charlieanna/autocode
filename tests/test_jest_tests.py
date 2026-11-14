"""Per-test results from Jest's own JSON report.

The reports below are Jest 30.5.2's output for a small project (``--json --outputFile``), trimmed of run
times, snapshot counts and failure detail. The proof cases run a stand-in for Jest that the test project
carries in its node_modules (tests/jest_stand_in.py), as ``npm test`` and as ``npx --no-install jest FILE``,
through the proof and the original-test gate; no provider runs. tools/autocode_jest_tests.py
"""

from __future__ import annotations

import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import autocode_command_guards as command_guards
import autocode_contract_identity as contracts
import autocode_jest_tests as jest_tests
import autocode_named_reporters as reporters
import autocode_plan_test_suite as plan_test_suite
import autocode_protected_oracles as guard
import autocode_regression as regression
import autocode_superseded_correction as correction
import autocode_test_cases as test_cases
import autocode_util as util
import autocode_verify as verify
from autocode_test_framework import Framework

from . import jest_stand_in
from .test_superseded_tests import Gate

ROOT = "/work/pager"


def case(title, status, *groups):
    return {"ancestorTitles": list(groups), "title": title, "fullName": " ".join([*groups, title]), "status": status,
            "failureMessages": [], "location": None, "numPassingAsserts": 1}  # fmt: skip


def suite(name, status, *cases, message=""):
    return {"name": f"{ROOT}/{name}", "status": status, "message": message, "summary": "", "assertionResults": [*cases]}


def report(*suites, success=None, **counts):
    cases = [test for found in suites for test in found["assertionResults"]]
    failed = sum(test["status"] == "failed" for test in cases)
    ok = all(found["status"] == "passed" for found in suites) if success is None else success
    return {
        "numFailedTestSuites": sum(found["status"] == "failed" for found in suites),
        "numFailedTests": failed,
        "numPassedTests": sum(test["status"] == "passed" for test in cases),
        "numPendingTests": sum(test["status"] == "pending" for test in cases),
        "numTodoTests": sum(test["status"] == "todo" for test in cases),
        "numTotalTestSuites": len(suites),
        "numTotalTests": len(cases),
        "openHandles": [],
        "success": ok,
        "wasInterrupted": False,
        "testResults": list(suites),
        **counts,
    }


# One file with nested describe blocks, a test.each whose two tests share a name, a skipped and a todo test.
PAGER = suite(
    "tests/a.test.js",
    "failed",
    case("exact", "passed", "pageCount", "rounding"),
    case("partial", "failed", "pageCount", "rounding"),
    case("works", "passed", "pageCount"),
    case("works", "passed", "pageCount"),
    case("skipped one", "pending", "pageCount"),
    case("todo one", "todo", "pageCount"),
    message="  ● pageCount › rounding › partial\n\n    expect(received).toBe(expected)",
)
# "Cannot find module '../src/missing.js'": the file never loaded, so no test in it ran.
UNLOADED = suite("tests/b.test.js", "failed", message="  ● Test suite failed to run\n\n    Cannot find module")
# afterAll threw: the file's one test ran and passed, and the file failed.
TEARDOWN = suite(
    "tests/c.test.js", "failed", case("fine", "passed"), message="  ● Test suite failed to run\n\n    teardown broke"
)
# beforeAll threw: Jest fails every test of the describe block.
SETUP = suite(
    "tests/d.test.js", "failed", case("one", "failed", "hooked"), message="  ● hooked › one\n\n    setup broke"
)
REPORT = report(PAGER, UNLOADED, TEARDOWN, SETUP, numRuntimeErrorTestSuites=2)
PASSING = report(suite("tests/a.test.js", "passed", case("exact", "passed", "pageCount", "rounding")))


class ResultsTests(unittest.TestCase):
    def read(self, value, tree=ROOT):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "suite.jest.json"
            path.write_text(value if isinstance(value, str) else json.dumps(value))
            return jest_tests.results(path, tree)

    def test_each_test_is_named_by_its_file_its_groups_and_its_title(self):
        self.assertEqual(
            {
                "passed": [
                    "tests/a.test.js::pageCount > rounding > exact",
                    "tests/a.test.js::pageCount > works",
                    "tests/c.test.js::fine",
                ],
                "failed": [
                    "tests/a.test.js::pageCount > rounding > partial",
                    "tests/b.test.js::[collection]",
                    "tests/c.test.js::[collection]",
                    "tests/d.test.js::hooked > one",
                ],
                "skipped": ["tests/a.test.js::pageCount > skipped one", "tests/a.test.js::pageCount > todo one"],
                # The file that never loaded ran nothing; the one whose teardown threw ran its test.
                "collection_errors": ["tests/b.test.js::[collection]", "tests/c.test.js::[collection]"],
                "uncollected": ["tests/b.test.js::[collection]"],
                "total": 9,
                "complete": True,
            },
            self.read(REPORT),
        )
        passing = self.read(PASSING)
        self.assertEqual(
            (["tests/a.test.js::pageCount > rounding > exact"], []), (passing["passed"], passing["failed"])
        )

    def test_tests_that_share_a_name_are_one_id_that_fails_when_any_of_them_fails(self):
        both = suite("tests/a.test.js", "failed", case("works", "passed"), case("works", "failed"))
        found = self.read(report(both))
        self.assertEqual(([], ["tests/a.test.js::works"], 1), (found["passed"], found["failed"], found["total"]))

    def test_a_run_that_failed_outside_every_test_and_file_is_a_failure_with_no_test_to_blame(self):
        # A coverage threshold or a global teardown: every test passed and Jest still exits 1.
        found = self.read({**PASSING, "success": False})
        self.assertEqual(["[jest runtime error]"], found["failed"])
        self.assertEqual(["[jest runtime error]"], found["collection_errors"])
        self.assertEqual([], found["uncollected"])

    def test_absent_ambiguous_or_inconsistent_evidence_gives_no_results(self):
        def changed(**fields):
            return {**copy.deepcopy(REPORT), **fields}

        def with_case(**fields):
            value = copy.deepcopy(PASSING)
            value["testResults"][0]["assertionResults"][0].update(fields)
            return value

        outside = copy.deepcopy(PASSING)
        outside["testResults"][0]["name"] = "/elsewhere/tests/a.test.js"
        relative = copy.deepcopy(PASSING)
        relative["testResults"][0]["name"] = "tests/a.test.js"
        twice = report(PAGER, PAGER)
        for name, value in {
            "not JSON": "Tests: 1 passed",
            "no report": {},
            "interrupted": changed(wasInterrupted=True),
            "another count of tests": changed(numTotalTests=7),
            "another count of failures": changed(numFailedTests=1),
            "another count of files": changed(numTotalTestSuites=3),
            "success with a failure": changed(success=True),
            "no success flag": changed(success="yes"),
            "an unknown state": with_case(status="flaky"),
            "an empty title": with_case(title=""),
            "groups that are not a list": with_case(ancestorTitles="pageCount"),
            "a file outside the tree": outside,
            "a file with no place": relative,
            "one file twice": twice,
            "an unknown file state": report({**PAGER, "status": "pending"}),
            "no test and no failure": report(),
        }.items():
            with self.subTest(name):
                self.assertIsNone(self.read(value))
        self.assertIsNone(jest_tests.results("/no/such/report.json", ROOT))
        self.assertIsNone(self.read(PASSING, None))


class CommandTests(unittest.TestCase):
    OUT = "/evidence/suite.jest.json"

    def test_a_plain_jest_command_writes_its_report_beside_its_usual_output(self):
        for command, expected in {
            "npx --no-install jest": "npx --no-install jest",
            "npx --no-install jest tests/a.test.js -t 'a b'": "npx --no-install jest tests/a.test.js -t 'a b'",
            "jest --coverage --ci": "jest --coverage --ci",
            "node_modules/.bin/jest": "node_modules/.bin/jest",
            "node node_modules/jest/bin/jest.js --runInBand": "node node_modules/jest/bin/jest.js --runInBand",
            "NODE_OPTIONS=--experimental-vm-modules npx --no-install jest": (
                "NODE_OPTIONS=--experimental-vm-modules npx --no-install jest"
            ),
            "cross-env CI=1 jest": "cross-env CI=1 jest",
        }.items():
            with self.subTest(command=command):
                self.assertEqual(f"{expected} --json --outputFile={self.OUT}", jest_tests.instrument(command, self.OUT))

    def test_other_commands_are_left_as_written(self):
        for command in (
            "npx jest",  # npx may install it; AutoCode never installs a tool into a project
            "jest --watch",
            "jest --watchAll=false",
            "jest --json",
            "jest --outputFile=out.json",
            "jest --listTests",
            "jest -o",
            "jest --onlyChanged",
            "jest --changedSince=main",
            "jest -- tests/a.test.js",
            "jest && eslint .",
            "FORCE_COLOR='1 2' jest",
            "vitest run",
            "npm test --silent",  # no tree to read its script from
            "",
            None,
        ):
            with self.subTest(command=command):
                self.assertIsNone(jest_tests.command_words(command))
                self.assertEqual(command, jest_tests.instrument(command, self.OUT))

    def test_npm_test_is_jest_when_the_package_script_is(self):
        with tempfile.TemporaryDirectory() as folder:
            package = Path(folder) / "package.json"
            for script, expected in {
                "jest": True,
                "jest --coverage": True,
                "NODE_OPTIONS=--experimental-vm-modules jest": True,
                "jest --watch": False,
                "jest && eslint .": False,
                "node --test tests/*.test.js": False,
            }.items():
                with self.subTest(script=script):
                    package.write_text(json.dumps({"scripts": {"test": script}}))
                    self.assertEqual(expected, jest_tests.command_words("npm test --silent", folder) is not None)
            package.write_text(json.dumps({"scripts": {"test": "jest"}}))
            self.assertEqual(
                f"npm test --silent -- --json --outputFile={self.OUT}",
                jest_tests.instrument("npm test --silent", self.OUT, folder),
            )
            self.assertEqual(
                f"npm test -- tests/a.test.js --json --outputFile={self.OUT}",
                jest_tests.instrument("npm test -- tests/a.test.js", self.OUT, folder),
            )
            self.assertIsNone(jest_tests.command_words("npm test -- --watch", folder))
            self.assertIsNone(jest_tests.command_words("npm run build", folder))

    def test_the_proof_expects_and_reads_jest_results(self):
        jest = Framework("jest", "npm test --silent")
        self.assertTrue(jest.per_test)
        self.assertIn("jest", test_cases.TITLE_RUNNERS)
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "package.json").write_text(json.dumps({"scripts": {"test": "jest"}}))
            for command in ("npm test --silent", "npx --no-install jest tests/a.test.js"):
                with self.subTest(command=command):
                    self.assertEqual("jest.json", reporters.extension(command, None, folder))
                    self.assertTrue(verify.expects_results(jest, command, folder))
                    # A native check runs its command as planned, and Jest's is kept as written.
                    self.assertTrue(verify.expects_results(jest, command, folder, scripts=False))
            self.assertFalse(verify.expects_results(jest, "npm run lint", folder))
            self.assertIsNone(reporters.extension("npm test --silent", None, None))
            path = Path(folder) / "suite-on-base.jest.json"
            path.write_text(json.dumps(REPORT).replace(ROOT, str(Path(folder).resolve())))
            self.assertEqual(9, verify.per_test_results(jest, {}, path, tree=folder)["total"])
            self.assertIsNone(verify.per_test_results(jest, {}, path, tree=Path(folder) / "elsewhere"))

    def test_a_plan_names_a_jest_test_and_does_not_guard_with_a_command(self):
        jest = Framework("jest", "npm test --silent")
        guards = [{"id": "AC2", "command": "npm test --silent"}]
        self.assertEqual([], command_guards.usable(guards, jest, ["tests/pager.test.js"]))
        self.assertIn("this project's jest suite names each test it runs", command_guards.problem(guards, jest, []))
        body = {"acceptance_criteria": [{"id": "AC1", "criterion": "c", "verification_method": "test: test_ac1_x"}]}
        self.assertIsNone(plan_test_suite.unnamed_cases(".", jest, [], body, lambda root: None))

    def test_composite_npm_test_script_is_instrumented_and_opaque_wrapper_is_not(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "package.json").write_text(json.dumps({"scripts": {"test": "jest && eslint ."}}))
            jest = Framework("jest", "npm test --silent")
            self.assertEqual("jest.json", reporters.extension("npm test --silent", root, root))
            self.assertTrue(reporters.reports("npm test --silent", root, root))
            self.assertTrue(reporters.suite_reports(jest, root))
            wrapped = reporters.instrument("npm test --silent", self.OUT, root, root)
            self.assertTrue(wrapped.startswith("npm exec --silent --call "), wrapped)
            self.assertIn("--json", wrapped)
            self.assertIn("eslint .", wrapped)
            # Without tree (NativeCheckExecution), composite script stays untouched.
            self.assertIsNone(reporters.instrument("npm test --silent", self.OUT, None, root))
            # Opaque react-scripts wrapper reports per_test False through suite_reports.
            (root / "package.json").write_text(json.dumps({"scripts": {"test": "react-scripts test"}}))
            self.assertFalse(reporters.suite_reports(jest, root))


SRC = "'use strict';\n\nfunction pageCount(total, size) {\n  return Math.ceil(total / size);\n}\n\nmodule.exports = { pageCount };\n"
# Rounds down: eleven items in pages of five is three pages, and this says two.
BROKEN = SRC.replace("Math.ceil", "Math.floor")
ONE_PAGE = SRC.replace("Math.ceil(total / size)", "Math.max(1, Math.ceil(total / size))")
HEAD = "'use strict';\n\nconst { pageCount } = require('../src/pager.js');\n\n"
EXACT = "test('returns the page count for an exact multiple', () => {\n  expect(pageCount(10, 5)).toBe(2);\n});\n"
PARTIAL = (
    "\ndescribe('rounding', () => {\n  test('counts a partial last page', () => {\n"
    "    expect(pageCount(11, 5)).toBe(3);\n  });\n});\n"
)
EMPTY = "\ntest('an empty list has no pages', () => {\n  expect(pageCount(0, 5)).toBe(0);\n});\n"
ONE = EMPTY.replace("has no pages", "still has one page").replace("toBe(0)", "toBe(1)")
EXACT_ID = "tests/pager.test.js::returns the page count for an exact multiple"
PARTIAL_ID = "tests/pager.test.js::rounding > counts a partial last page"
EMPTY_ID = "tests/pager.test.js::an empty list has no pages"
ENTRY = {"file": "tests/pager.test.js", "test": "an empty list has no pages"}


def node(test):
    return unittest.skipUnless(shutil.which("node") and shutil.which("npm"), "the project's suite is npm test")(test)


@node
class ProofTests(unittest.TestCase):
    """A Jest project's proof is by test, as a Vitest or node:test project's is."""

    def prove(self, seed, files, *methods, kind="bugfix", supersedes=None):
        project = jest_stand_in.project(seed)
        self.addCleanup(project.close)
        framework = verify.detect_framework(project.root)
        self.assertEqual(("jest", "npm test --silent"), (framework.name, framework.suite))
        project.write(files)
        rows = [{"id": f"AC{n}", "criterion": "pages", "verification_method": m} for n, m in enumerate(methods, 1)]
        state = {"workspace": str(project.root), "base_commit": project.base, "settings": {"regression": {}}}
        state["goal_contract"] = {
            "body": {"task_kind": kind, "acceptance_criteria": rows, "open_blocking_questions": []}
        }
        if supersedes:
            rows[0]["supersedes"] = supersedes
            sealed = {"task_id": "t", "revision": 1, "body": state["goal_contract"]["body"]}
            sealed["hash"] = util.digest(sealed)
            event = {"kind": "goal_approval", "actor": "user_cli", "token": contracts.token(sealed)}
            state["goal_contract"] = {**sealed, "approval_status": "approved", "approval_event": event}
            state["goal_contract"]["origin"] = "planner_draft"
            state["user_events"] = [event]
        with mock.patch.object(verify.diff_coverage, "collect", return_value=None):
            return regression.prove(state, project.root, project.evidence / "proof")

    def test_a_fix_is_proved_by_its_new_test_and_guarded_by_an_existing_one_each_by_its_title(self):
        proof = self.prove(
            {"src/pager.js": BROKEN, "tests/pager.test.js": HEAD + EXACT},
            {"src/pager.js": SRC, "tests/pager.test.js": HEAD + EXACT + PARTIAL},
            "test: counts a partial last page",
            "guard: returns the page count for an exact multiple",
        )
        self.assertEqual(verify.PASS, proof["verdict"], proof)
        self.assertEqual([PARTIAL_ID], proof["fail_to_pass"])
        self.assertEqual({"AC1": [PARTIAL_ID], "AC2": [EXACT_ID]}, proof["case_tests"])
        self.assertIn("--json --outputFile=", proof["checks"]["suite_on_candidate"]["command"])

    def test_a_fix_that_leaves_the_named_test_failing_is_not_proved(self):
        proof = self.prove(
            {"src/pager.js": BROKEN, "tests/pager.test.js": HEAD + EXACT},
            {"src/pager.js": BROKEN.replace("Math.floor", "Math.trunc"), "tests/pager.test.js": HEAD + EXACT + PARTIAL},
            "test: counts a partial last page",
        )
        self.assertEqual(verify.FAIL, proof["verdict"], proof)

    def test_a_failing_test_the_project_already_has_shows_the_repair_by_name(self):
        # "The test 'counts a partial last page' fails. Fix src/pager.js. Do not change the tests."
        proof = self.prove(
            {"src/pager.js": BROKEN, "tests/pager.test.js": HEAD + EXACT + PARTIAL},
            {"src/pager.js": SRC},
            "test: counts a partial last page",
            "guard: returns the page count for an exact multiple",
        )
        self.assertEqual(verify.PASS, proof["verdict"], proof)
        self.assertEqual([PARTIAL_ID], proof["fail_to_pass"])
        self.assertIn(
            f"Existing tests that fail on the original code pass with the change: {PARTIAL_ID}", proof["notes"]
        )

    def test_a_refactor_is_guarded_by_the_tests_the_project_already_has(self):
        seed = {"src/pager.js": SRC, "tests/pager.test.js": HEAD + EXACT + PARTIAL}
        guards = ("guard: counts a partial last page", "guard: returns the page count for an exact multiple")
        kept = SRC.replace("return Math.ceil(total / size);", "const pages = Math.ceil(total / size);\n  return pages;")
        proof = self.prove(seed, {"src/pager.js": kept}, *guards, kind="build")
        self.assertEqual(verify.PASS, proof["verdict"], proof)
        self.assertEqual({"AC1": [PARTIAL_ID], "AC2": [EXACT_ID]}, proof["case_tests"])
        broken = self.prove(seed, {"src/pager.js": BROKEN}, *guards, kind="build")
        self.assertNotEqual(verify.PASS, broken["verdict"], broken)
        self.assertIn(PARTIAL_ID, " ".join(broken["failures"]))

    def test_a_test_the_plan_replaces_may_be_renamed_and_no_other_test_may_go(self):
        # "pageCount(0, 5) should return 1. Update the tests to match."
        seed = {"src/pager.js": SRC, "tests/pager.test.js": HEAD + EXACT + EMPTY}
        files = {"src/pager.js": ONE_PAGE, "tests/pager.test.js": HEAD + EXACT + ONE}
        method = "test: an empty list still has one page"
        proof = self.prove(seed, files, method, kind="build", supersedes=[ENTRY])
        self.assertEqual(verify.PASS, proof["verdict"], proof)
        self.assertIn(f"Tests the approved plan replaces are no longer in the suite: {EMPTY_ID}", proof["notes"])
        unlisted = self.prove(seed, files, method, kind="build")
        self.assertEqual(verify.FAIL, unlisted["verdict"], unlisted)
        self.assertIn(f"renamed or missing): {EMPTY_ID}", " ".join(unlisted["failures"]))


@node
class GateTests(unittest.TestCase):
    """The original tests, run against the change, name the one that fails."""

    def setUp(self):
        self.made = jest_stand_in.project({"src/pager.js": SRC, "tests/pager.test.js": HEAD + EXACT + EMPTY})
        self.addCleanup(self.made.close)
        self.project = self.made.root
        self.run_dir = self.project / ".autocode/runs/jest"
        self.state = {"settings": {}, "workspace": str(self.project), "user_events": []}
        guard.reconcile(
            self.state,
            self.state["settings"],
            SimpleNamespace(run_dir=None),
            self.project,
            self.run_dir,
            is_test_path=verify.is_test_path,
            discover_command=lambda: "npm test --silent",
        )

    approve = Gate.approve

    def replay(self, test=HEAD + EXACT + EMPTY.replace("toBe(0)", "toBe(1)"), code=ONE_PAGE):
        self.made.write({"src/pager.js": code, "tests/pager.test.js": test})
        return guard.replay(self.state, self.project, self.run_dir / "replays", verify.scratch_run, timeout=60)

    def test_the_listed_test_is_the_one_failure_the_gate_accepts(self):
        self.approve([ENTRY], method="test: an empty list has no pages")
        result = self.replay()
        self.assertEqual("PASS", result["verdict"])
        self.assertEqual([EMPTY_ID], result["superseded"]["excused_failures"])
        # And when the change renames it, as the plan's replaced test may be.
        self.assertEqual("PASS", self.replay(HEAD + EXACT + ONE)["verdict"])

    def test_an_unlisted_change_is_refused_by_name_and_drafted_as_the_test_the_plan_replaces(self):
        self.approve(None, method="test: an empty list has no pages")
        with self.assertRaisesRegex(ValueError, "Failing original tests: " + EMPTY_ID) as caught:
            self.replay()
        found = correction.proposal(self.state, caught.exception.receipt, self.project)
        self.assertEqual([{"criterion": "AC1", **ENTRY}], found["tests"])

    def test_another_original_test_that_fails_keeps_the_gate_closed(self):
        self.approve([ENTRY], method="test: an empty list has no pages")
        with self.assertRaisesRegex(ValueError, "Failing original tests: .*returns the page count for an exact"):
            self.replay(
                (HEAD + EXACT + EMPTY).replace("toBe(0)", "toBe(1)").replace("toBe(2)", "toBe(3)"),
                ONE_PAGE.replace("Math.max(1, Math.ceil(total / size))", "Math.max(1, Math.ceil(total / size)) + 1"),
            )


if __name__ == "__main__":
    unittest.main()
