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
import re
import shlex
import shutil
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

import autocode_companion_suites as companion_suites
import autocode_package_scope as package_scope
import autocode_project_kinds as project_kinds
import autocode_protected_oracles as guard
import autocode_protected_project_suite as project_suite
import autocode_regression as regression
import autocode_replay_checks as replay_checks
import autocode_test_homes as test_homes
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


def prove(case, seed, change, *, package=None, rows=None):
    """The run's own proof of ``change``: no plan command, and ``--package`` when the run named one."""
    project = Project(seed)
    case.addCleanup(project.close)
    project.write(change)
    row = {"id": "AC1", "criterion": "pages", "verification_method": "a regression test fails on the original code"}
    sealed = {"task_id": "task-1", "revision": 1, "body": {"task_kind": "bugfix", "acceptance_criteria": rows or [row]}}
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


def run_gate(case, seed, change, command, name, *, typed=False):
    """Bind ``command`` and the original tests of ``seed`` as a run does when it starts, apply ``change``
    and run the original-test gate. ``typed``: the person gave the command (``--test-command``)."""
    project = Project(seed)
    case.addCleanup(project.close)
    run = project.root.parent / "run"
    state = {"settings": {"regression": {"test_command": command}} if typed else {}}
    guard.reconcile(
        state,
        state["settings"],
        SimpleNamespace(run_dir=None),
        project.root,
        run,
        is_test_path=verify.is_test_path,
        discover_command=lambda: command,
    )
    case.assertEqual(command, state["settings"]["protected_tests"]["command"])
    case.assertIn(name, state["settings"]["protected_tests"]["files"])
    project.write(change)
    return guard.replay(state, project.root, run / "replays", verify.scratch_run, timeout=60)


def run_task(case, seed, change, command, *, rows=None):
    """(proof, gate): the proof of ``change`` and then the original-test gate, on one run state as in a run.
    ``command`` is the suite the run bound for its original tests when it started; ``gate()`` runs the gate."""
    project = Project(seed)
    case.addCleanup(project.close)
    run = project.root.parent / "run"
    row = {"id": "AC1", "criterion": "pages", "verification_method": "a regression test fails on the original code"}
    sealed = {"task_id": "task-1", "revision": 1, "body": {"task_kind": "bugfix", "acceptance_criteria": rows or [row]}}
    state = {
        "workspace": str(project.root),
        "base_commit": project.base,
        "iteration": 1,
        "stages": [],
        "history": [],
        "settings": {"regression": {}},
        "goal_contract": {**sealed, "hash": util.digest(sealed), "approval_status": "approved"},
    }
    guard.reconcile(
        state,
        state["settings"],
        SimpleNamespace(run_dir=None),
        project.root,
        run,
        is_test_path=verify.is_test_path,
        discover_command=lambda: command,
    )
    project.write(change)
    with mock.patch.object(verify.diff_coverage, "collect", return_value=None):
        proof = regression.prove(state, project.root, run)
    return proof, lambda: guard.replay(state, project.root, run / "replays", verify.scratch_run, timeout=60)


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
        return run_gate(self, seed, change, command, name)

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


class NestedPythonOriginalGateTests(unittest.TestCase):
    """The Python half of the same repository. The bound Python runner can run ``api/``'s tests, but from
    the root: ``python3 -m unittest -v api/tests/test_orders.py`` cannot import ``orders`` from ``api/``,
    where the tests are written to run and where the regression proof runs them
    (``cd api && python3 -m unittest -v tests/test_orders.py``). The proof passed and the gate failed with
    "Protected tests changed and the original gate did not pass" (checked without a model, 2026-11-15)."""

    SEED = {"README.md": "# shop\n", "web/package.json": PACKAGE, "web/index.js": INDEX, **API}
    NAME = "api/tests/test_orders.py"
    ORIGINAL = API[NAME]
    NEW = "\n    def test_cents_are_rounded(self):\n        self.assertEqual(0.3, total([0.1, 0.2]))\n"
    ROUNDED = "def total(lines):\n    return round(sum(lines), 2)\n"

    def python(self, *arguments):
        return shlex.join([sys.executable, "-m", *arguments])

    def test_the_changed_tests_run_inside_their_own_project(self):
        change = {"api/orders.py": self.ROUNDED, self.NAME: self.ORIGINAL + self.NEW}
        result = run_gate(self, self.SEED, change, self.python("unittest", "discover", "-v"), self.NAME)
        self.assertEqual("PASS", result["verdict"], result)
        self.assertEqual([self.NAME], result["changed_tests"])
        self.assertEqual(
            "cd api && " + self.python("unittest", "-v", "tests/test_orders.py"), result["replayed_command"]
        )
        self.assertEqual((0, 0), (result["candidate"]["exit_code"], result["original"]["exit_code"]))

    def test_a_weakened_original_assertion_still_fails_the_gate(self):
        change = {
            "api/orders.py": "def total(lines):\n    return sum(lines) + 1\n",
            self.NAME: self.ORIGINAL.replace("assertEqual(5,", "assertEqual(6,"),
        }
        with self.assertRaisesRegex(ValueError, "original gate did not pass") as raised:
            run_gate(self, self.SEED, change, self.python("unittest", "discover", "-v"), self.NAME)
        receipt = raised.exception.receipt
        self.assertTrue(receipt["replayed_command"].startswith("cd api && "), receipt)
        self.assertEqual(0, receipt["candidate"]["exit_code"])
        self.assertNotEqual(0, receipt["original"]["exit_code"])

    def test_the_bound_runner_is_the_one_that_runs_them_there(self):
        project = Project(self.SEED)
        self.addCleanup(project.close)
        pytest = self.python("pytest", "-q")
        self.assertEqual(
            "cd api && " + self.python("pytest", "-q", "tests/test_orders.py"),
            guard.replay_command(pytest, [self.NAME], project.root),
        )

    def test_a_command_the_person_typed_runs_where_they_typed_it(self):
        # These tests are written to run from the root (``from api.orders import total``), and the person
        # says so with --test-command, which the proof runs from the root too.
        from_root = self.ORIGINAL.replace("from orders import", "from api.orders import")
        self.assertNotEqual(self.ORIGINAL, from_root)
        seed = {**self.SEED, "api/__init__.py": "", "api/tests/__init__.py": "", self.NAME: from_root}
        change = {"api/orders.py": self.ROUNDED, self.NAME: from_root + self.NEW}
        typed = self.python("unittest", "discover", "-v", "-s", "api/tests", "-t", ".")
        result = run_gate(self, seed, change, typed, self.NAME, typed=True)
        self.assertEqual("PASS", result["verdict"], result)
        self.assertEqual(self.python("unittest", "-v", self.NAME), result["replayed_command"])
        # So does a command the person corrected the original tests' command to.
        state = {"settings": {}, "user_events": [{"kind": "protected_tests_revised"}]}
        self.assertFalse(project_suite.detected(state))
        self.assertTrue(project_suite.detected({"settings": {}, "user_events": [{"kind": "plan_approved"}]}))
        self.assertFalse(project_suite.detected({"settings": {"regression": {"test_command": typed}}}))

    def test_the_root_still_runs_what_is_its_own(self):
        unittest_suite = self.python("unittest", "discover", "-v")
        from_root = self.python("unittest", "-v")
        files = {
            **self.SEED,
            "jobs/pyproject.toml": API["api/pyproject.toml"],
            "jobs/tests/test_jobs.py": self.ORIGINAL,
            "scripts/tests/test_scripts.py": self.ORIGINAL,
            "tests/fixtures/app/pyproject.toml": API["api/pyproject.toml"],
            "tests/fixtures/app/tests/test_app.py": self.ORIGINAL,
        }
        project = Project(files)
        self.addCleanup(project.close)
        for changed in (
            [self.NAME, "jobs/tests/test_jobs.py"],  # two projects: no one directory holds both
            ["scripts/tests/test_scripts.py"],  # no manifest: not a project
            ["tests/fixtures/app/tests/test_app.py"],  # a fixture of the root's own tests
        ):
            with self.subTest(changed=changed):
                self.assertEqual(
                    shlex.join([*shlex.split(from_root), *sorted(changed)]),
                    guard.replay_command(unittest_suite, changed, project.root),
                )
        # A runner that cannot be narrowed to the files runs the whole bound suite where it was bound.
        unknown = self.python("unittest", "discover", "-v", "--unknown-option")
        self.assertEqual(unknown, guard.replay_command(unknown, [self.NAME], project.root))
        # A runner that names something by where it runs is narrowed there.
        for bound, narrowed in (
            ("env PYTHONPATH=api " + unittest_suite, "env PYTHONPATH=api " + from_root),
            (".venv/bin/python -m unittest discover -v", ".venv/bin/python -m unittest -v"),
            (self.python("pytest", "-q", "--rootdir", "."), self.python("pytest", "-q", "--rootdir", ".")),
            (self.python("pytest", "-q", "--ignore=web"), self.python("pytest", "-q", "--ignore=web")),
        ):
            with self.subTest(bound=bound):
                self.assertEqual(
                    shlex.join([*shlex.split(narrowed), self.NAME]),
                    guard.replay_command(bound, [self.NAME], project.root),
                )
        # A root that is a project of its own runs its tests from the root, as its suite does.
        owned = Project({**self.SEED, "pyproject.toml": API["api/pyproject.toml"]})
        self.addCleanup(owned.close)
        self.assertEqual(
            shlex.join([*shlex.split(from_root), self.NAME]),
            guard.replay_command(unittest_suite, [self.NAME], owned.root),
        )


BACKEND = {
    "README.md": "# shop\n",
    "frontend/package.json": PACKAGE,
    "frontend/index.js": INDEX,
    "backend/requirements.txt": "requests\n",
    "backend/pager.py": "def page_count(total, size):\n    return total // size\n",
    "backend/tests/test_pager.py": (
        "import unittest\n\nfrom pager import page_count\n\n\nclass PagerTests(unittest.TestCase):\n"
        "    def test_an_exact_multiple(self):\n        self.assertEqual(2, page_count(10, 5))\n"
    ),
}
BARE = {name: text for name, text in BACKEND.items() if name != "backend/requirements.txt"}
BACKEND_FIXED = "def page_count(total, size):\n    return (total + size - 1) // size\n"
BACKEND_NEW = "\n    def test_a_partial_last_page(self):\n        self.assertEqual(3, page_count(11, 5))\n"


class DeclaredPythonProjectTests(unittest.TestCase):
    """A backend kept as ``backend/requirements.txt``, code and ``tests/``, beside ``frontend/``, with no
    manifest anywhere above its tests. They say ``from pager import page_count`` and run from ``backend/``.
    The proof ran the root's suite, which finds no test it can import: "The regression command ran no
    passing tests", "the new tests only fail to import or collect" (checked without a model, 2026-11-15).
    No rule knows this layout: the proof runs the changed tests and finds where they load
    (autocode_test_homes)."""

    NAME = "backend/tests/test_pager.py"
    CHANGE = {"backend/pager.py": BACKEND_FIXED, NAME: BACKEND[NAME] + BACKEND_NEW}

    def python(self, *arguments):
        return shlex.join([sys.executable, "-m", *arguments])

    def assert_proven_inside(self, proof):
        self.assertEqual((verify.PASS, [], []), (proof["verdict"], proof["failures"], proof["unverified"]), proof)
        commands = proof["commands"]
        self.assertRegex(commands["suite"], r"^cd backend && \S+ -m unittest discover -v -s tests$")
        self.assertRegex(commands["regression"], r"^cd backend && \S+ -m unittest -v tests/test_pager\.py$")
        self.assertEqual(["tests.test_pager.PagerTests.test_a_partial_last_page"], proof["fail_to_pass"])
        found = [note for note in proof["notes"] if note.startswith(f"The changed test {self.NAME} does not load")]
        self.assertEqual(1, len(found), proof["notes"])
        self.assertRegex(
            found[0],
            r"does not load from the repository root \(`\S+ -m unittest -v backend/tests/test_pager\.py`\); it loads "
            r"from backend/ \(`cd backend && \S+ -m unittest -v tests/test_pager\.py`\), so that is where it runs$",
        )

    def test_a_fix_in_a_directory_that_declares_its_dependencies_is_proven_from_inside_it(self):
        self.assert_proven_inside(prove(self, BACKEND, self.CHANGE))

    def test_a_pipfile_declares_them_too(self):
        seed = {name: text for name, text in BACKEND.items() if name != "backend/requirements.txt"}
        self.assert_proven_inside(prove(self, {**seed, "backend/Pipfile": "[packages]\n"}, self.CHANGE))

    def test_so_is_one_that_declares_nothing(self):
        self.assert_proven_inside(prove(self, BARE, self.CHANGE))

    def test_and_one_inside_another_languages_project(self):
        # package.json at the root: the root's suite is Node's, which runs no Python test.
        seed = {**BARE, "package.json": PACKAGE, "index.js": INDEX}
        proof = prove(self, seed, self.CHANGE)
        self.assertEqual((verify.PASS, [], []), (proof["verdict"], proof["failures"], proof["unverified"]), proof)
        self.assertRegex(proof["commands"]["regression"], r"^cd backend && \S+ -m unittest -v tests/test_pager\.py$")
        self.assertTrue(
            any(
                f"The changed test {self.NAME} is run by no suite of the repository root" in note
                for note in proof["notes"]
            ),
            proof["notes"],
        )

    def test_its_original_tests_run_inside_it(self):
        proof, gate = run_task(self, BACKEND, self.CHANGE, self.python("unittest", "discover", "-v"))
        self.assertEqual(verify.PASS, proof["verdict"], proof)
        result = gate()
        self.assertEqual("PASS", result["verdict"], result)
        self.assertRegex(result["replayed_command"], r"^cd backend && \S+ -m unittest -v tests/test_pager\.py$")

    def weakened(self, seed, bound):
        """A change that breaks the code and edits the original assertion to suit it: (proof, the gate's failure)."""
        test = BACKEND[self.NAME].replace("assertEqual(2, page_count(10, 5))", "assertEqual(3, page_count(10, 5))")
        change = {"backend/pager.py": "def page_count(total, size):\n    return total // size + 1\n", self.NAME: test}
        proof, gate = run_task(self, seed, change, bound)
        with self.assertRaisesRegex(ValueError, "original gate did not pass") as raised:
            gate()
        return proof, raised.exception.receipt

    def test_a_weakened_original_assertion_still_fails_the_gate_there(self):
        _proof, receipt = self.weakened(BARE, self.python("unittest", "discover", "-v"))
        self.assertTrue(receipt["replayed_command"].startswith("cd backend && "), receipt)
        self.assertEqual(0, receipt["candidate"]["exit_code"])
        self.assertNotEqual(0, receipt["original"]["exit_code"])

    def test_it_fails_the_gate_inside_another_languages_project_too(self):
        # The bound suite is the root's `npm test`, which runs no Python test: it could not catch this,
        # with or without a manifest in backend/. The gate runs the test where the proof saw it load.
        node = {**BARE, "package.json": PACKAGE, "index.js": INDEX}
        manifest = {"backend/pyproject.toml": "[project]\nname = 'backend'\nversion = '1.0.0'\n"}
        for label, seed in (("declares nothing", node), ("has a manifest", {**node, **manifest})):
            with self.subTest(label):
                _proof, receipt = self.weakened(seed, "npm test --silent")
                self.assertTrue(receipt["replayed_command"].startswith("cd backend && "), receipt)
                self.assertEqual(0, receipt["candidate"]["exit_code"])
                self.assertNotEqual(0, receipt["original"]["exit_code"])

    def test_a_changed_helper_of_its_tests_runs_its_whole_suite(self):
        seed = {**BARE, "backend/tests/helpers.py": "PER_PAGE = 5\n"}
        change = {**self.CHANGE, "backend/tests/helpers.py": "PER_PAGE = 5\nPAGES = 3\n"}
        proof, gate = run_task(self, seed, change, self.python("unittest", "discover", "-v"))
        self.assertEqual(verify.PASS, proof["verdict"], proof)
        result = gate()
        self.assertEqual("PASS", result["verdict"], result)
        self.assertRegex(result["replayed_command"], r"^cd backend && \S+ -m unittest discover -v -s tests$")

    def test_a_new_test_that_passes_on_the_original_code_is_still_no_proof(self):
        passing = "\n    def test_one_page(self):\n        self.assertEqual(1, page_count(5, 5))\n"
        proof = prove(self, BACKEND, {"backend/pager.py": BACKEND_FIXED, self.NAME: BACKEND[self.NAME] + passing})
        self.assertEqual(verify.FAIL, proof["verdict"])
        self.assertTrue(any("No test fails on the unfixed base code" in text for text in proof["failures"]), proof)

    def test_a_test_that_loads_nowhere_fails_as_before(self):
        broken = BACKEND[self.NAME].replace("from pager import", "from pagers import")
        proof = prove(self, BACKEND, {"backend/pager.py": BACKEND_FIXED, self.NAME: broken + BACKEND_NEW})
        self.assertEqual(verify.FAIL, proof["verdict"])
        self.assertRegex(proof["commands"]["regression"], r"^\S+ -m unittest -v backend/tests/test_pager\.py$")
        self.assertFalse(any("so that is where it runs" in note for note in proof["notes"]), proof["notes"])

    def test_a_package_its_tests_import_by_name_is_still_proven_from_the_root(self):
        tests_from_root = BACKEND[self.NAME].replace("from pager import", "from backend.pager import")
        seed = {**BACKEND, "backend/__init__.py": "", "backend/tests/__init__.py": "", self.NAME: tests_from_root}
        proof = prove(self, seed, {"backend/pager.py": BACKEND_FIXED, self.NAME: tests_from_root + BACKEND_NEW})
        self.assertEqual((verify.PASS, [], []), (proof["verdict"], proof["failures"], proof["unverified"]), proof)
        self.assertRegex(proof["commands"]["suite"], r"^\S+ -m unittest discover -v$")
        self.assertEqual(["backend.tests.test_pager.PagerTests.test_a_partial_last_page"], proof["fail_to_pass"])
        self.assertFalse(any("so that is where it runs" in note for note in proof["notes"]), proof["notes"])

    def test_tests_beside_the_code_are_proven_from_inside_too(self):
        beside = "backend/test_pager.py"
        seed = {**{name: text for name, text in BARE.items() if name != self.NAME}, beside: BARE[self.NAME]}
        proof = prove(self, seed, {"backend/pager.py": BACKEND_FIXED, beside: BARE[self.NAME] + BACKEND_NEW})
        self.assertEqual((verify.PASS, [], []), (proof["verdict"], proof["failures"], proof["unverified"]), proof)
        self.assertRegex(proof["commands"]["regression"], r"^cd backend && \S+ -m unittest -v test_pager\.py$")
        self.assertEqual(["test_pager.PagerTests.test_a_partial_last_page"], proof["fail_to_pass"])

    def test_a_replay_takes_the_recorded_homes_and_derives_the_commands_the_recording_ran(self):
        # A replay runs nothing, so it cannot look where the tests load. It reads what the recorded proof kept
        # and derives its commands from that; the recording's commands are then compared with them.
        recorded = prove(self, BARE, self.CHANGE)
        homes = recorded["test_homes"]
        self.assertEqual([self.NAME], sorted(homes))
        self.assertEqual(("backend", "", True), tuple(homes[self.NAME][key] for key in ("project", "from", "moved")))
        self.assertEqual(homes, util.read(recorded["path"])["test_homes"])
        checks = replay_checks
        for kept, inside in ((homes, "cd backend && "), (None, "")):
            with self.subTest(recorded_homes=bool(kept)):
                project = Project(BARE)
                self.addCleanup(project.close)
                project.write(self.CHANGE)
                state = {
                    "workspace": str(project.root),
                    "base_commit": project.base,
                    "iteration": 1,
                    "stages": [],
                    "history": [],
                    "settings": {"regression": {}},
                    "goal_contract": {"body": {"task_kind": "bugfix"}},
                }
                derived = {}
                with (
                    mock.patch.object(checks, "simulated", return_value=True),
                    mock.patch.object(checks, "require_supported"),
                    mock.patch.object(checks, "test_homes", return_value=kept),
                    mock.patch.object(checks, "regression", side_effect=lambda *_, **named: derived.update(named)),
                    mock.patch.object(verify, "scratch_run", side_effect=AssertionError("a replay ran a command")),
                ):
                    regression.prove(state, project.root, project.root.parent / "run")
                self.assertTrue(derived["suite"].startswith(inside), derived)
                same = [derived["suite"], derived["regression_command"]] == [
                    recorded["commands"]["suite"],
                    recorded["commands"]["regression"],
                ]
                self.assertEqual(bool(kept), same, derived)

    def test_the_rules_alone_know_only_a_manifest(self):
        project = Project({**BACKEND, "backend/svc/setup.py": "", "backend/svc/tests/test_svc.py": ""})
        self.addCleanup(project.close)
        self.assertEqual([], package_scope.projects_around(project.root, "backend/tests"))
        self.assertEqual(["backend/svc"], package_scope.projects_around(project.root, "backend/svc/tests"))
        self.assertEqual(companion_suites.ROOT, companion_suites.project_of(project.root, self.NAME))


def pager_tests(module, expected=2):
    return (
        f"import unittest\n\nfrom {module} import page_count\n\n\nclass PagerTests(unittest.TestCase):\n"
        f"    def test_an_exact_multiple(self):\n        self.assertEqual({expected}, page_count(10, 5))\n"
    )


def partial(number):
    return f"\n    def test_ac{number}_partial(self):\n        self.assertEqual(3, page_count(11, 5))\n"


class TwoPythonProjectsTests(unittest.TestCase):
    """One change that fixes ``api/`` and ``worker/``, two Python projects side by side under a root with no
    manifest. Each project's tests import its own code and run from inside it. The root's suite claimed
    both by their kind and ran them from the root, where neither can import its code: the proof failed with
    "the new tests only fail to import or collect", and the gate of the original tests failed on both
    (checked without a model, 2026-11-15)."""

    MANIFEST = "[project]\nname = 'shop'\nversion = '1.0.0'\n"
    API, WORKER = "api/tests/test_pager.py", "worker/tests/test_paging.py"
    SEED = {
        "README.md": "# shop\n",
        "api/pyproject.toml": MANIFEST,
        "api/pager.py": BACKEND["backend/pager.py"],
        API: pager_tests("pager"),
        "worker/pyproject.toml": MANIFEST,
        "worker/paging.py": BACKEND["backend/pager.py"],
        WORKER: pager_tests("paging"),
    }
    FIXES = {"api/pager.py": BACKEND_FIXED, "worker/paging.py": BACKEND_FIXED}
    CHANGE = {**FIXES, API: pager_tests("pager") + partial(1), WORKER: pager_tests("paging") + partial(2)}
    ROWS = [
        {"id": "AC1", "criterion": "the API counts a partial page", "verification_method": "test: test_ac1_partial"},
        {"id": "AC2", "criterion": "so does the worker", "verification_method": "test: test_ac2_partial"},
    ]

    def python(self, *arguments):
        return shlex.join([sys.executable, "-m", *arguments])

    def test_each_projects_tests_are_proven_with_its_own_suite(self):
        proof = prove(self, self.SEED, self.CHANGE, rows=self.ROWS)
        self.assertEqual((verify.PASS, [], []), (proof["verdict"], proof["failures"], proof["unverified"]), proof)
        self.assertRegex(proof["commands"]["suite"], r"^cd api && \S+ -m unittest discover -v -s tests$")
        self.assertEqual(
            {
                "AC1": ["tests.test_pager.PagerTests.test_ac1_partial"],
                "AC2": ["tests.test_paging.PagerTests.test_ac2_partial"],
            },
            proof["case_tests"],
        )
        self.assertTrue(any("that project's own suite `cd worker && " in note for note in proof["notes"]), proof)

    def test_a_project_whose_new_test_passes_on_the_original_code_is_still_not_proven(self):
        passing = "\n    def test_ac2_partial(self):\n        self.assertEqual(1, page_count(5, 5))\n"
        change = {**self.CHANGE, self.WORKER: pager_tests("paging") + passing}
        proof = prove(self, self.SEED, change, rows=self.ROWS)
        self.assertEqual(verify.FAIL, proof["verdict"])
        self.assertTrue(any("worker/" in text for text in proof["failures"]), proof["failures"])

    def settled(self, files, tests):
        """(project, state, where): the homes of ``tests`` found by running them as the proof does, and
        ``where``: the directory of the proof's own suite and the tests each other project's suite proves."""
        project = Project({"README.md": "# shop\n", **files})
        self.addCleanup(project.close)
        detect = verify.detect_framework
        root = detect(project.root)
        chosen = companion_suites.test_suites(project.root, root, tests, detect, is_code=verify.is_code_path)
        runnable = [test for test in tests if verify.is_code_path(test)]
        state, runs = {}, []

        def probe(_suite, command, label):
            runs.append(command)
            return verify.scratch_run(
                project.root, project.root.parent / "homes" / str(len(runs)), command=command, timeout=60
            )

        test_homes.settle(state, project.root, root, chosen, runnable, detect, probe)
        framework, suites, _ = test_homes.place(state, project.root, root, chosen, runnable, detect)
        where = (getattr(framework, "directory", ""), {suite["project"]: suite["files"] for suite in suites})
        return project, state, where

    def test_which_suite_proves_which_tests(self):
        both = [self.API, self.WORKER]
        at_root = {"tests/test_shop.py": pager_tests("api.pager")}
        for label, files, tests, expected in (
            ("two projects", self.SEED, both, ("api", {"worker": [self.WORKER]})),
            (
                "a project and the root",
                {**self.SEED, **at_root},
                [self.API, "tests/test_shop.py"],
                ("", {"api": [self.API]}),
            ),
            # The root's manifest does not make their imports work from the root: they run where they load.
            (
                "the root is a project too",
                {**self.SEED, "pyproject.toml": self.MANIFEST},
                both,
                ("api", {"worker": [self.WORKER]}),
            ),
            ("one project", self.SEED, [self.API], ("api", {})),
            (
                "a directory with no suite of its own",
                {**self.SEED, "docs/pyproject.toml": self.MANIFEST, "docs/conf.py": "", "docs/test_notes.txt": ""},
                [self.API, "docs/test_notes.txt"],
                ("api", {}),
            ),
        ):
            with self.subTest(label):
                self.assertEqual(expected, self.settled(files, tests)[2])

    def test_the_original_tests_of_each_project_run_inside_it(self):
        bound = self.python("unittest", "discover", "-v")
        proof, gate = run_task(self, self.SEED, self.CHANGE, bound, rows=self.ROWS)
        self.assertEqual(verify.PASS, proof["verdict"], proof)
        result = gate()
        self.assertEqual("PASS", result["verdict"], result)
        self.assertRegex(
            result["replayed_command"],
            r"^cd api && \S+ -m unittest -v tests/test_pager\.py ; "
            r"cd worker && \S+ -m unittest -v tests/test_paging\.py$",
        )
        for half, ran in (("candidate", 4), ("original", 2)):
            self.assertEqual([0, 0], [run["exit_code"] for run in result[half]["suites"]], result[half])
            self.assertEqual(ran, result[half]["results"]["total"])

    def test_a_weakened_original_assertion_in_one_project_still_fails_the_gate(self):
        change = {
            "api/pager.py": BACKEND_FIXED,
            self.API: pager_tests("pager") + partial(1),
            "worker/paging.py": "def page_count(total, size):\n    return total // size + 1\n",
            self.WORKER: pager_tests("paging", expected=3),
        }
        _proof, gate = run_task(self, self.SEED, change, self.python("unittest", "discover", "-v"), rows=self.ROWS)
        with self.assertRaisesRegex(ValueError, "original gate did not pass") as raised:
            gate()
        receipt = raised.exception.receipt
        self.assertEqual([0, 0], [run["exit_code"] for run in receipt["candidate"]["suites"]])
        self.assertEqual([0, 1], [run["exit_code"] for run in receipt["original"]["suites"]])
        self.assertIn("tests.test_paging.PagerTests.test_an_exact_multiple", str(raised.exception))

    def test_which_commands_the_gate_runs(self):
        seed = {**self.SEED, "tests/test_shop.py": pager_tests("api.pager")}
        bound = self.python("unittest", "discover", "-v")
        from_root = self.python("unittest", "-v")
        both = [self.API, self.WORKER]
        api, worker = (
            "cd api && PY -m unittest -v tests/test_pager.py",
            "cd worker && PY -m unittest -v tests/test_paging.py",
        )

        def parts(project, state, command, changed, **options):
            found = guard.replay_parts(command, changed, project.root, homes=test_homes.away(state), **options)
            return [re.sub(r"(?<=&& )\S+(?= -m unittest)", "PY", part) if part else part for part in found]

        project, state, _ = self.settled(seed, both)
        self.assertEqual([api, worker], parts(project, state, bound, both))
        self.assertEqual([api], parts(project, state, bound, [self.API]))
        # A file that is no test of a project (a helper of its tests) runs that project's whole suite.
        self.assertEqual(
            ["cd api && PY -m unittest discover -v -s tests", worker],
            parts(project, state, bound, [*both, "api/tests/helpers.py"]),
        )
        self.assertEqual([bound], parts(project, state, bound, []))
        # The tests of a moved project replace a suite of the root that cannot be given files.
        self.assertEqual([api, worker], parts(project, state, "make test", both))
        self.assertEqual([api, worker], parts(project, state, None, both))
        # A command the person typed runs everything where they typed it.
        self.assertEqual([f"{from_root} {self.API} {self.WORKER}"], parts(project, state, bound, both, detected=False))
        # A test the proof left at the root runs with the bound command, beside the moved one.
        project, state, _ = self.settled(seed, [self.API, "tests/test_shop.py"])
        self.assertEqual(
            [from_root + " tests/test_shop.py", api], parts(project, state, bound, [self.API, "tests/test_shop.py"])
        )
        # Before any proof ran nothing was observed: the bound runner and the rules decide, as before.
        self.assertEqual(
            [f"{from_root} {self.API} {self.WORKER}"], guard.replay_parts(bound, both, project.root, homes={})
        )


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
