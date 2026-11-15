"""A project kept one directory down has its tests where a project at the top keeps them.

A repository of several projects (``web/`` beside ``api/``, ``packages/pager/``) gives each its own manifest,
and each keeps its tests the way a lone project does: a ``test/`` directory, or one ``test.js``, beside its
code. Those were tests only at the top of the repository, so a bug fix in ``web/`` that added its regression
test to ``web/test/pager.js`` failed its proof with "No regression test was added or changed", with and
without ``--package web``, and that file was not among the original tests a run keeps (survey section 45).

These cases run real regression proofs in real Git projects.
tools/autocode_project_kinds.py (project_roots, is_test_path, test_rule), tools/autocode_python_test_roles.py
"""

import json
import shlex
import shutil
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

import autocode_project_kinds as project_kinds
import autocode_protected_oracles as guard
import autocode_regression as regression
import autocode_test_role_inventory as role_inventory
import autocode_util as util
import autocode_verify as verify

from .test_verify import Project

INDEX = (
    "'use strict';\n\nfunction pageCount(total, size) {\n  return Math.floor(total / size);\n}\n\n"
    "module.exports = { pageCount };\n"
)
INDEX_FIXED = INDEX.replace("Math.floor", "Math.ceil")
PACKAGE = json.dumps({"name": "pager", "version": "1.0.0", "scripts": {"test": "node --test"}}, indent=2) + "\n"
NEW = "\ntest('a partial last page is counted', () => {\n  assert.strictEqual(pageCount(11, 5), 3);\n});\n"
NO_PROOF = "\ntest('one page', () => {\n  assert.strictEqual(pageCount(5, 5), 1);\n});\n"


def tests(code):
    return (
        "'use strict';\nconst test = require('node:test');\nconst assert = require('node:assert');\n"
        f"const {{ pageCount }} = require('{code}');\n\n"
        "test('an exact multiple needs no extra page', () => {\n  assert.strictEqual(pageCount(10, 5), 2);\n});\n"
    )


def prove(case, seed, change, *, package=None):
    """The run's own proof of ``change``: no plan command, and ``--package`` when the run named one."""
    project = Project(seed)
    case.addCleanup(project.close)
    project.write(change)
    row = {"id": "AC1", "criterion": "pages", "verification_method": "a regression test fails on the original code"}
    sealed = {"task_id": "task-1", "revision": 1, "body": {"task_kind": "bugfix", "acceptance_criteria": [row]}}
    state = {
        "workspace": str(project.root),
        "base_commit": project.base,
        "iteration": 1,
        "stages": [],
        "history": [],
        "settings": {"regression": {}, **({"package": package} if package else {})},
        "goal_contract": {**sealed, "hash": util.digest(sealed), "approval_status": "approved"},
    }
    with mock.patch.object(verify.diff_coverage, "collect", return_value=None):
        return regression.prove(state, project.root, project.root.parent / "run")


@unittest.skipUnless(shutil.which("node") and shutil.which("npm"), "the nested project's suite is npm test")
class NestedProjectProofTests(unittest.TestCase):
    WEB = {"README.md": "# shop\n", "web/package.json": PACKAGE, "web/index.js": INDEX}

    def nested(self, name, code, new=NEW, **options):
        seed = {**self.WEB, name: tests(code)}
        return prove(self, seed, {"web/index.js": INDEX_FIXED, name: tests(code) + new}, **options)

    def assert_proven(self, proof, test, regression_command):
        self.assertEqual((verify.PASS, [], []), (proof["verdict"], proof["failures"], proof["unverified"]), proof)
        self.assertEqual([test], proof["test_files"])
        self.assertEqual("cd web && npm test --silent", proof["commands"]["suite"])
        self.assertEqual(regression_command, proof["commands"]["regression"])

    def test_a_test_directory_of_a_project_one_directory_down_proves_the_fix(self):
        proof = self.nested("web/test/pager.js", "../index.js")
        self.assert_proven(proof, "web/test/pager.js", "cd web && node --test test/pager.js")
        self.assertEqual(["test/pager.js::a partial last page is counted"], proof["fail_to_pass"])

    def test_it_proves_the_fix_when_the_run_named_the_package(self):
        proof = self.nested("web/test/pager.js", "../index.js", package="web")
        self.assert_proven(proof, "web/test/pager.js", "cd web && node --test test/pager.js")

    def test_one_test_file_beside_the_nested_projects_code_proves_the_fix(self):
        proof = self.nested("web/test.js", "./index.js")
        self.assert_proven(proof, "web/test.js", "cd web && node --test test.js")

    def test_a_new_test_that_passes_on_the_original_code_is_still_no_proof(self):
        proof = self.nested("web/test/pager.js", "../index.js", new=NO_PROOF)
        self.assertEqual(verify.FAIL, proof["verdict"])
        self.assertTrue(any("No test fails on the unfixed base code" in text for text in proof["failures"]), proof)

    def test_a_test_directory_inside_a_projects_code_is_still_product_code(self):
        # lib/ holds no manifest: lib/test/ may be the code of a ``test`` command.
        seed = {"package.json": PACKAGE, "lib/index.js": INDEX, "lib/test/pager.js": tests("../index.js")}
        change = {"lib/index.js": INDEX_FIXED, "lib/test/pager.js": tests("../index.js") + NEW}
        proof = prove(self, seed, change)
        self.assertEqual(verify.FAIL, proof["verdict"])
        self.assertEqual(
            ["No regression test was added or changed, so the bug is not shown to be reproduced"], proof["failures"]
        )

    def test_a_manifest_the_change_adds_makes_no_code_a_test(self):
        seed = {"package.json": PACKAGE, "lib/index.js": INDEX, "lib/test/pager.js": tests("../index.js")}
        change = {
            "lib/index.js": INDEX_FIXED,
            "lib/package.json": PACKAGE,
            "lib/test/pager.js": tests("../index.js") + NEW,
        }
        proof = prove(self, seed, change)
        self.assertEqual(verify.FAIL, proof["verdict"])
        self.assertEqual(
            ["No regression test was added or changed, so the bug is not shown to be reproduced"], proof["failures"]
        )


class NestedProjectBindingTests(unittest.TestCase):
    def test_the_original_tests_of_a_run_hold_each_projects_tests(self):
        project = Project(
            {
                "package.json": PACKAGE,
                "index.js": INDEX,
                "test/root.js": tests("../index.js"),
                "web/package.json": PACKAGE,
                "web/index.js": INDEX,
                "web/test/pager.js": tests("../index.js"),
                "web/lib/test/run.js": "module.exports = () => 0;\n",
                "tools/test.js": tests("../index.js"),
            }
        )
        self.addCleanup(project.close)
        self.assertEqual(
            ["test/root.js", "web/test/pager.js"], sorted(guard.inventory(project.root, verify.is_test_path))
        )
        self.assertEqual(
            ["web/test/pager.js"], sorted(guard.inventory(project.root, verify.is_test_path, package="web"))
        )


API = {
    "api/pyproject.toml": "[project]\nname = 'shop-api'\nversion = '1.0.0'\n",
    "api/orders.py": "def total(lines):\n    return sum(lines)\n",
    "api/tests/test_orders.py": (
        "import unittest\n\nfrom orders import total\n\n\nclass TotalTests(unittest.TestCase):\n"
        "    def test_total(self):\n        self.assertEqual(5, total([2, 3]))\n"
    ),
}


@unittest.skipUnless(shutil.which("node"), "the nested project's tests run with node --test")
class NestedProjectOriginalGateTests(unittest.TestCase):
    """The original tests a change edits run again with the original assertions put back
    (autocode_protected_oracles.replay). The run binds the root's suite for that, which in a repository of
    api/ (Python) beside web/ (Node) is Python's and runs no test of web/: a real run's proof passed and
    this gate then failed with "Protected tests changed and the original gate did not pass" (2026-11-15)."""

    WEB = {"README.md": "# shop\n", "web/package.json": PACKAGE, "web/index.js": INDEX}
    ORIGINAL = tests("../index.js")
    TARGETED = "cd web && node --test test/pager.js"

    def gate(self, seed, change, command, name="web/test/pager.js"):
        project = Project(seed)
        self.addCleanup(project.close)
        run = project.root.parent / "run"
        state = {"settings": {}}
        guard.reconcile(
            state,
            state["settings"],
            SimpleNamespace(run_dir=None),
            project.root,
            run,
            is_test_path=verify.is_test_path,
            discover_command=lambda: command,
        )
        self.assertEqual(command, state["settings"]["protected_tests"]["command"])
        self.assertIn(name, state["settings"]["protected_tests"]["files"])
        project.write(change)
        return guard.replay(state, project.root, run / "replays", verify.scratch_run, timeout=60)

    def python_suite(self):
        return shlex.join([sys.executable, "-m", "unittest", "discover", "-v"])

    def test_the_changed_tests_run_with_their_own_projects_suite(self):
        # web/pager.test.js is a test by its name, on master too, where this gate failed for it as well.
        for name, code, targeted in (
            ("web/test/pager.js", "../index.js", self.TARGETED),
            ("web/pager.test.js", "./index.js", "cd web && node --test pager.test.js"),
        ):
            with self.subTest(name=name):
                seed = {**self.WEB, **API, name: tests(code)}
                change = {"web/index.js": INDEX_FIXED, name: tests(code) + NEW}
                result = self.gate(seed, change, self.python_suite(), name)
                self.assertEqual("PASS", result["verdict"], result)
                self.assertEqual([name], result["changed_tests"])
                self.assertEqual(targeted, result["replayed_command"])
                self.assertEqual((0, 0), (result["candidate"]["exit_code"], result["original"]["exit_code"]))

    def test_a_weakened_original_assertion_still_fails_the_gate(self):
        seed = {**self.WEB, **API, "web/test/pager.js": self.ORIGINAL}
        change = {
            "web/index.js": INDEX.replace("Math.floor(total / size)", "Math.floor(total / size) + 1"),
            "web/test/pager.js": self.ORIGINAL.replace("pageCount(10, 5), 2", "pageCount(10, 5), 3"),
        }
        with self.assertRaisesRegex(ValueError, "original gate did not pass") as raised:
            self.gate(seed, change, self.python_suite())
        receipt = raised.exception.receipt
        self.assertEqual(self.TARGETED, receipt["replayed_command"])
        self.assertEqual(0, receipt["candidate"]["exit_code"])
        self.assertNotEqual(0, receipt["original"]["exit_code"])

    def test_they_run_with_it_where_the_root_has_no_suite_at_all(self):
        seed = {**self.WEB, "web/test/pager.js": self.ORIGINAL}
        change = {"web/index.js": INDEX_FIXED, "web/test/pager.js": self.ORIGINAL + NEW}
        result = self.gate(seed, change, None)
        self.assertEqual("PASS", result["verdict"], result)
        self.assertEqual(self.TARGETED, result["replayed_command"])
        self.assertNotIn("plan_command", result)

    def test_the_bound_suite_still_runs_what_it_can_run(self):
        project = Project(
            {
                **self.WEB,
                **API,
                "web/test/pager.js": self.ORIGINAL,
                "tests/fixtures/app/package.json": PACKAGE,
                "tests/fixtures/app/app.test.js": self.ORIGINAL,
                "tests/test_fixtures.py": "def test_fixture():\n    assert True\n",
            }
        )
        self.addCleanup(project.close)
        python = self.python_suite()
        for command, changed in (
            (python, ["api/tests/test_orders.py", "web/test/pager.js"]),  # two projects: no one command runs both
            (python, ["tests/fixtures/app/app.test.js"]),  # a fixture of the root's own tests
            (python, ["web/test/gone.js"]),
            ("npm test --silent", ["web/test/pager.js"]),  # the root's suite may run its packages' tests
            ("make test", ["web/test/pager.js"]),
            (None, ["api/tests/test_orders.py"]),
        ):
            with self.subTest(command=command, changed=changed):
                self.assertEqual(command, guard.replay_command(command, changed, project.root))
        self.assertEqual(self.TARGETED, guard.replay_command(python, ["web/test/pager.js"], project.root))


class ProjectRootRuleTests(unittest.TestCase):
    FILES = (
        "package.json",
        "web/package.json",
        "services/api/go.mod",
        "gems/money/Gemfile",
        "docs/pyproject.toml",
        "test/fixtures/app/package.json",
    )

    def test_the_projects_below_the_root_are_the_directories_that_hold_a_manifest(self):
        # docs/ is a Python project: autocode_python_test_roles reads its test/ directory.
        self.assertEqual(
            {"web", "services/api", "gems/money", "test/fixtures/app"}, project_kinds.project_roots(self.FILES)
        )
        self.assertEqual(frozenset(), project_kinds.project_roots(["package.json", "src/index.js", "Makefile"]))

    def test_what_holds_tests_at_the_top_holds_them_at_the_top_of_each_project(self):
        rule = project_kinds.test_rule(self.FILES)
        for path in (
            "web/test/pager.js",
            "web/test/unit/pager.js",
            "web/test/fixtures/orders.json",
            "web/test.js",
            "web/tests.ts",
            "web/spec/pager.js",
            "services/api/test/helpers.go",
            "gems/money/test/helper.rb",
            "gems/money/spec/support/money.rb",
            "test/fixtures/app/index.js",  # under the repository's own test/, as before
            "web/src/pager.test.js",
            "test/root.js",
        ):
            self.assertTrue(rule(path), path)
            self.assertTrue(role_inventory.test_rule(self.FILES)(path), path)
        for path in (
            "web/index.js",
            "web/lib/test/run.js",  # no project at web/lib
            "web/lib/test.js",
            "web/testing/helpers.js",
            "tools/test/run.js",  # no project at tools
            "tools/test.js",
            "services/test/helpers.go",  # the project is services/api
            "docs/test/build.py",  # a Python project is read by autocode_python_test_roles
            "docs/test/expected/index.html",
            "docs/test.py",
            "web/test/helper.py",  # and so is a Python file anywhere
        ):
            self.assertFalse(rule(path), path)

    def test_without_the_tree_only_the_top_of_the_repository_counts(self):
        self.assertFalse(project_kinds.is_test_path("web/test/pager.js"))
        self.assertFalse(verify.is_test_path("web/test.js"))
        self.assertIs(project_kinds.is_test_path, project_kinds.test_rule(["package.json", "index.js"]))


if __name__ == "__main__":
    unittest.main()
