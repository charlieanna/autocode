"""A change with tests in two projects of one repository is proven by both suites (autocode_companion_tests).

A real run (Claude models, 2026-11-14) was asked for one change across a Python package and a Node package
of the same repository, with tests on both sides. The plan named both test files, the plan check refused it
three times ("it runs the plan's tests with one suite"), and the Investigator asked the person how to
proceed. The selection and plan cases are pure; the proof cases run real Python and Node tests in real Git
trees and never launch a provider.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import autocode_companion_suites as companions
import autocode_goal_lifecycle as lifecycle
import autocode_package_scope as package_scope
import autocode_plan_test_suite as plan_test_suite
import autocode_regression as regression
import autocode_verify as verify
import goal_fixtures
from autocode_test_framework import Framework

from tests.test_verify import Project

WEB = "packages/web"
API_TEST, WEB_TEST = "tests/test_payloads.py", f"{WEB}/tests/format.test.js"
PAYLOADS = 'def note_payload(note):\n    return {"id": note["id"], "title": note["title"]}\n'
API_TESTS = (
    "import unittest\n\nfrom payloads import note_payload\n\n\nclass PayloadTests(unittest.TestCase):\n"
    '    def test_title(self):\n        self.assertEqual("Milk", note_payload({"id": 7, "title": "Milk"})["title"])\n'
)
FORMAT = "exports.noteLine = (payload) => payload.title;\n"
WEB_TESTS = (
    "const assert = require('node:assert/strict');\nconst test = require('node:test');\n"
    "const { noteLine } = require('../src/format.js');\n\n"
    "test('a note is shown by its title', () => {\n  assert.equal(noteLine({ title: 'Milk' }), 'Milk');\n});\n"
)
# A Python project at the root and a Node package beside its code, each with its own suite.
SEED = {
    "payloads.py": PAYLOADS,
    "tests/__init__.py": "",
    API_TEST: API_TESTS,
    f"{WEB}/package.json": '{"name": "web", "private": true, "scripts": {"test": "node --test"}}\n',
    f"{WEB}/src/format.js": FORMAT,
    WEB_TEST: WEB_TESTS,
}
# The change: the API returns `archived`, and the line shows an archived note in brackets.
ARCHIVED = PAYLOADS.replace('note["title"]}', 'note["title"], "archived": bool(note.get("archived"))}')
API_PROOF = API_TESTS + (
    "\n    def test_archived_is_a_boolean(self):\n"
    '        self.assertIs(True, note_payload({"id": 1, "title": "x", "archived": 1})["archived"])\n'
)
BRACKETS = "exports.noteLine = (payload) => (payload.archived ? `[${payload.title}]` : payload.title);\n"
WEB_PROOF = WEB_TESTS + (
    "\ntest('an archived note is shown in brackets', () => {\n"
    "  assert.equal(noteLine({ title: 'Milk', archived: true }), '[Milk]');\n});\n"
)
CHANGE = {"payloads.py": ARCHIVED, API_TEST: API_PROOF, f"{WEB}/src/format.js": BRACKETS, WEB_TEST: WEB_PROOF}
CRITERIA = [
    {"id": "C1", "criterion": "The API returns archived", "verification_method": "test: test_archived_is_a_boolean"},
    {
        "id": "C2",
        "criterion": "An archived note is in brackets",
        "verification_method": "test: an archived note is shown in brackets",
    },
]
TITLE = "a note is shown by its title"
NODE = Framework("node", "npm test --silent", node_files=("tests/format.test.js",))
PYTHON = Framework("unittest", "python3 -m unittest discover")


def detect(directory):
    return verify.detect_framework(directory, python=sys.executable)


def state(project, *, kind="build", criteria=CRITERIA):
    return {
        "base_commit": project.base,
        "settings": {"regression": {"python": sys.executable}},
        "iteration": 1,
        "stages": [],
        "history": [],
        "goal_contract": {"body": {"task_kind": kind, "acceptance_criteria": criteria, "milestones": [{"id": "M1"}]}},
    }


class SelectionTests(unittest.TestCase):
    def repository(self, files):
        project = Project(files)
        self.addCleanup(project.close)
        return project

    def test_tests_the_proofs_suite_cannot_run_go_to_the_suite_of_the_project_around_them(self):
        project = self.repository(SEED)
        root = detect(project.root)
        framework, suites, notes = companions.test_suites(project.root, root, [WEB_TEST, API_TEST], detect)
        self.assertIs(root, framework)
        self.assertEqual([(WEB, [WEB_TEST])], [(suite["project"], suite["files"]) for suite in suites])
        self.assertEqual(f"cd {WEB} && npm test --silent", suites[0]["framework"].suite)
        self.assertEqual(
            [
                f"The changed tests {WEB_TEST} are in {WEB}/, whose tests the proof's suite `{root.suite}` cannot "
                f"run, so that project's own suite `cd {WEB} && npm test --silent` proves them as it would alone"
            ],
            notes,
        )

    def test_one_suite_that_runs_every_test_is_chosen_as_before(self):
        project = self.repository(SEED)
        root = detect(project.root)
        for tests in ([API_TEST], [WEB_TEST], []):
            with self.subTest(tests=tests):
                framework, suites, notes = companions.test_suites(project.root, root, tests, detect)
                expected = package_scope.for_tests(project.root, root, tests, detect) or root
                self.assertEqual((expected.suite, [], []), (framework.suite, suites, notes))
        self.assertEqual((None, [], []), companions.test_suites(project.root, None, [], detect))
        # A runner that may hold any test (`make test`) is never said to leave one out.
        make = Framework("make", "make test")
        self.assertEqual(
            (make, [], []), companions.test_suites(project.root, make, [WEB_TEST, API_TEST], lambda d: None)
        )

    def test_tests_in_two_projects_below_a_root_that_runs_neither_each_get_their_own_suite(self):
        files = {f"backend/{path}": text for path, text in SEED.items() if not path.startswith(WEB)}
        files.update({path.replace("packages/web", "web"): text for path, text in SEED.items() if path.startswith(WEB)})
        project = self.repository(
            {**files, "backend/pyproject.toml": "[project]\nname = 'api'\n", "go.mod": "module x\n"}
        )
        tests = ["backend/tests/test_payloads.py", "web/tests/format.test.js"]
        root = Framework("go", "go test ./...")
        framework, suites, _ = companions.test_suites(project.root, root, tests, detect)
        # The first project's suite is the proof's; the other proves its own tests.
        self.assertEqual("backend", framework.directory)
        self.assertEqual([("web", ["web/tests/format.test.js"])], [(s["project"], s["files"]) for s in suites])

    def test_a_test_no_suite_runs_is_noted_and_left_to_the_proofs_suite(self):
        project = self.repository({path: text for path, text in SEED.items() if not path.endswith("package.json")})
        root = detect(project.root)
        framework, suites, notes = companions.test_suites(project.root, root, [WEB_TEST, API_TEST], detect)
        self.assertEqual((root, []), (framework, suites))
        self.assertEqual(
            [
                f"The changed tests {WEB_TEST} are in the repository root, and neither the proof's suite "
                f"`{root.suite}` nor a suite of that project runs them, so they were not run"
            ],
            notes,
        )

    def test_a_changed_fixture_or_helper_is_no_test_of_its_own(self):
        helpers = {f"{WEB}/tests/helpers.js": "exports.x = 1;\n", f"{WEB}/tests/fixtures/notes.json": "{}\n"}
        project = self.repository({**SEED, **helpers})
        root = detect(project.root)
        for extra in (*helpers, "tests/data/notes.json", "tests/conftest.py"):
            with self.subTest(extra=extra):
                found = companions.test_suites(
                    project.root, root, [API_TEST, extra], detect, is_code=verify.is_code_path
                )
                self.assertEqual((root, [], []), found)

    def test_a_project_with_its_own_proof_gets_no_second_run_of_its_suite(self):
        project = self.repository(SEED)
        code, tests = [f"{WEB}/src/format.js"], [WEB_TEST, API_TEST]
        found, _ = companions.find(project.root, PYTHON, PYTHON, code, tests, lambda directory: NODE)
        self.assertEqual([WEB], [at for at, _ in found])
        self.assertEqual(
            ([], []), companions.find(project.root, PYTHON, PYTHON, code, tests, lambda directory: NODE, proven={WEB})
        )


class PlanTests(unittest.TestCase):
    def check(self, files, *paths):
        project = Project(files)
        self.addCleanup(project.close)
        body = goal_fixtures.body()
        body["open_blocking_questions"] = []
        body["milestones"][0]["affected_paths"] = ["payloads.py", f"{WEB}/src/format.js", *paths]
        run = {"task": "Archive notes", "workspace": str(project.root), "settings": {}, "answers": {}}
        lifecycle.validate_body({**run, "user_events": [], "brief_feedback": []}, body, ready=True)

    def test_a_plan_with_tests_in_two_projects_that_each_have_a_suite_is_accepted(self):
        self.check(SEED, API_TEST, WEB_TEST)
        self.check(SEED, API_TEST, f"{WEB}/tests/archived.test.js")  # a test file the change adds

    def test_a_test_in_a_directory_without_a_suite_of_its_kind_is_still_refused(self):
        refusal = (
            r"The regression proof would never run the plan's tests packages/web/tests/format\.test\.js: it runs "
            r"the plan's tests with the unittest suite `.*`, which runs Python tests, and a test of another kind "
            r"with the suite of the project directory around it, and no such directory has a suite that runs these"
        )
        without_manifest = {path: text for path, text in SEED.items() if not path.endswith("package.json")}
        with self.assertRaisesRegex(ValueError, refusal):
            self.check(without_manifest, API_TEST, WEB_TEST)
        # A project whose own suite runs another kind of test would not run a new JavaScript test either.
        ruby = {path: text for path, text in without_manifest.items() if path != WEB_TEST}
        ruby.update({f"{WEB}/Gemfile": "source 'https://rubygems.org'\ngem 'rspec'\n", f"{WEB}/spec/a_spec.rb": ""})
        with self.assertRaisesRegex(ValueError, refusal):
            self.check(ruby, API_TEST, WEB_TEST)

    def test_only_the_tests_no_suite_would_run_are_named(self):
        project = Project(SEED)
        self.addCleanup(project.close)
        stray = "docs/examples/demo.test.js"
        self.assertEqual(
            [stray],
            plan_test_suite.problems(project.root, detect(project.root), [API_TEST, WEB_TEST, stray], detect),
        )


@unittest.skipUnless(shutil.which("node") and shutil.which("npm"), "Node and npm run the package's suite")
class ProofTests(unittest.TestCase):
    def prove(self, change, *, kind="build", criteria=CRITERIA):
        project = Project(SEED)
        self.addCleanup(project.close)
        project.write(change)
        with tempfile.TemporaryDirectory() as run_dir:
            return regression.prove(state(project, kind=kind, criteria=criteria), project.root, Path(run_dir))

    def test_each_projects_tests_are_proven_by_its_own_suite(self):
        proof = self.prove(CHANGE)
        self.assertEqual(verify.PASS, proof["verdict"], proof)
        self.assertEqual("unittest", proof["framework"]["name"])
        # The root's suite showed its own test fail on the original code; the package's suite showed the other.
        self.assertEqual(["tests.test_payloads.PayloadTests.test_archived_is_a_boolean"], proof["fail_to_pass"])
        self.assertEqual(
            {
                "C1": ["tests.test_payloads.PayloadTests.test_archived_is_a_boolean"],
                "C2": ["tests/format.test.js::an archived note is shown in brackets"],
            },
            proof["case_tests"],
        )
        on_base = proof["checks"][f"companion_tests:{WEB}:regression_on_base"]
        on_change = proof["checks"][f"companion_tests:{WEB}:regression_on_candidate"]
        self.assertRegex(on_change["command"], rf"^cd {WEB} && node .*--test tests/format\.test\.js$")
        self.assertNotEqual(0, on_base["exit_code"], on_base)
        self.assertEqual(0, on_change["exit_code"], on_change)
        # The package's whole suite ran before and after as part of its proof, and once only.
        self.assertIn(f"companion_tests:{WEB}:suite_on_candidate", proof["checks"])
        self.assertFalse([label for label in proof["checks"] if label.startswith("companion_suite_")])
        self.assertIn(
            "Test case C2's test tests/format.test.js::an archived note is shown in brackets did not pass on the "
            f"original code and passes with the change in the suite of {WEB}/ (`cd {WEB} && node --test "
            "tests/format.test.js`), which the proof's suite cannot run",
            proof["notes"],
        )
        self.assertEqual([WEB], list(proof["execution_context"]["companion_tests"]))
        self.assertEqual([WEB_TEST], proof["execution_context"]["companion_tests"][WEB]["files"])

    def test_a_test_in_the_other_project_that_the_original_code_passes_proves_nothing(self):
        # Before, the other project's new test ran only with the change, as part of its suite: this proof passed.
        vacuous = WEB_TESTS + "\ntest('an archived note is shown in brackets', () => {\n  assert.ok(noteLine);\n});\n"
        proof = self.prove({**CHANGE, WEB_TEST: vacuous}, kind="bugfix", criteria=CRITERIA[:1])
        self.assertEqual(verify.FAIL, proof["verdict"], proof)
        self.assertEqual(
            [
                f"In {WEB}/: No test fails on the unfixed base code and passes with the fix, so the tests do not reproduce the bug"
            ],
            proof["failures"],
        )
        self.assertTrue(proof["case_tests"]["C1"], proof)
        # A criterion that names it has no test either.
        proof = self.prove({**CHANGE, WEB_TEST: vacuous}, kind="bugfix")
        self.assertEqual([], proof["case_tests"]["C2"], proof)

    def test_a_test_in_the_other_project_that_fails_with_the_change_fails_the_proof(self):
        proof = self.prove({**CHANGE, f"{WEB}/src/format.js": FORMAT})
        self.assertEqual(verify.FAIL, proof["verdict"], proof)
        self.assertTrue([text for text in proof["failures"] if text.startswith(f"In {WEB}/: ")], proof["failures"])
        self.assertEqual([], proof["case_tests"]["C2"], proof)

    def test_a_change_that_breaks_the_other_projects_existing_test_fails_the_proof(self):
        broken = "exports.noteLine = (payload) => (payload.archived ? `[${payload.title}]` : 'x');\n"
        proof = self.prove({**CHANGE, f"{WEB}/src/format.js": broken})
        self.assertEqual(verify.FAIL, proof["verdict"], proof)
        self.assertIn("a note is shown by its title", " ".join(map(str, proof["failures"])))

    def test_a_guard_names_the_other_projects_test_by_its_title(self):
        # unittest reads no title as a test name, so the guard was looked for as test_c3_... and found nowhere:
        # the real run built and proved the change and then asked the person how to bind the guard.
        guard = {"id": "C3", "criterion": "A note keeps its line", "verification_method": f"guard: {TITLE}"}
        kept = f"tests/format.test.js::{TITLE}"
        # In the test file the change adds to: it ran on the original code with the change's tests.
        proof = self.prove(CHANGE, criteria=[*CRITERIA, guard])
        self.assertEqual((verify.PASS, [kept]), (proof["verdict"], proof["case_tests"]["C3"]), proof)
        # In a file the change leaves alone, beside a new test file: the package's whole suite ran it.
        added = WEB_PROOF.replace(WEB_TESTS.split("\n\n", 1)[1], "")
        proof = self.prove(
            {**CHANGE, WEB_TEST: WEB_TESTS, f"{WEB}/tests/archived.test.js": added}, criteria=[*CRITERIA, guard]
        )
        self.assertEqual((verify.PASS, [kept]), (proof["verdict"], proof["case_tests"]["C3"]), proof)
        self.assertEqual(["tests/archived.test.js::an archived note is shown in brackets"], proof["case_tests"]["C2"])
        # In a project the change does not test: its suite runs the file that spells the title.
        proof = self.prove({"payloads.py": ARCHIVED, API_TEST: API_PROOF}, criteria=[CRITERIA[0], guard])
        self.assertEqual((verify.PASS, [kept]), (proof["verdict"], proof["case_tests"]["C3"]), proof)
        self.assertIn(f"companion_guards_on_base:{WEB}", proof["checks"])
        # A title no test has is still no guard.
        missing = {**guard, "verification_method": "guard: a note is shown by its body"}
        proof = self.prove(CHANGE, criteria=[*CRITERIA, missing])
        self.assertEqual((verify.FAIL, []), (proof["verdict"], proof["case_tests"]["C3"]), proof)

    def test_a_guard_whose_test_fails_in_the_other_project_is_broken(self):
        guard = {"id": "C3", "criterion": "A note keeps its line", "verification_method": f"guard: {TITLE}"}
        broken = "exports.noteLine = (payload) => (payload.archived ? `[${payload.title}]` : 'x');\n"
        proof = self.prove({**CHANGE, f"{WEB}/src/format.js": broken}, criteria=[*CRITERIA, guard])
        self.assertEqual((verify.FAIL, []), (proof["verdict"], proof["case_tests"]["C3"]), proof)

    def test_a_change_with_tests_in_one_project_is_proven_as_before(self):
        proof = self.prove({"payloads.py": ARCHIVED, API_TEST: API_PROOF}, criteria=CRITERIA[:1])
        self.assertEqual(verify.PASS, proof["verdict"], proof)
        self.assertFalse([label for label in proof["checks"] if label.startswith("companion_tests")])
        self.assertNotIn("companion_tests", proof["execution_context"])


if __name__ == "__main__":
    unittest.main()
