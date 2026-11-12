"""A run scoped to one package of a monorepo (#711): detection, the suite's directory, the
protected inventory and the proof, with real Git and real test subprocesses."""

from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import autocode_native_test_names as native_names
import autocode_package_scope as package_scope
import autocode_preflight_worker as preflight_worker
import autocode_proof_stability as proof_stability
import autocode_protected_oracles as protected_oracles
import autocode_regression as regression
import autocode_run_view as run_view
import autocode_scratch_suites as scratch_suites
import autocode_task_preflight as task_preflight
import autocode_util as util
import autocode_verify as verify
import scenario_references as references
import task_scenarios as scenarios

from tests.test_verify import Project

GREET, OTHER = "packages/greet", "packages/other"
OTHER_TEST = (
    "import unittest\n\nfrom other import answer\n\n\nclass TestOther(unittest.TestCase):\n"
    "    def test_answer(self):\n        self.assertEqual(answer(), 42)\n"
)
# Two packages, each with its own tests. The greeting package's tests start greet.py by a path relative
# to the working directory, so they pass only when they run from the package directory.
MONOREPO = {
    f"{GREET}/greet.py": scenarios.BUGFIX_SEED["greet.py"],
    f"{GREET}/test_greet.py": scenarios.BUGFIX_SEED["test_greet.py"],
    f"{OTHER}/other.py": "def answer():\n    return 42\n",
    f"{OTHER}/test_other.py": OTHER_TEST,
    "README.md": "# Two packages\n",
}
FIX = {
    f"{GREET}/greet.py": references.BUGFIX_REFERENCE["greet.py"],
    f"{GREET}/test_greet.py": references.BUGFIX_REFERENCE["test_greet.py"],
}
# A Go module in its own package directory: detection finds it only when the run is scoped to it.
GO_PACKAGE = {
    "packages/svc/go.mod": "module example.test/svc\n\ngo 1.21\n",
    "packages/svc/svc.go": "package svc\n\nfunc Double(n int) int { return 2 * n }\n",
    "packages/svc/svc_test.go": 'package svc\n\nimport "testing"\n\n'
    "func TestDouble(t *testing.T) {\n\tif Double(2) != 4 {\n\t\tt.Fatal(Double(2))\n\t}\n}\n",
}
SEED_TESTS = sorted(f"test_greet.TestGreet.{name}" for name in scenarios.BUGFIX_SEED_TESTS)
# One Go module at the repository root, with its packages in subdirectories. Detection in a package needs
# the package's own go.mod, so it finds no suite in packages/svc (docs/workflow.md, "Not covered yet").
GO_ZERO_TEST = (
    'package svc\n\nimport "testing"\n\nfunc TestZero(t *testing.T) {\n\tif Double(0) != 0 {\n\t\tt.Fatal()\n\t}\n}\n'
)
GO_ROOT_MODULE = {
    "go.mod": "module example.test/mono\n\ngo 1.21\n",
    "packages/svc/svc.go": "package svc\n\nfunc Double(n int) int { return 3 * n }\n",
    "packages/svc/svc_test.go": GO_ZERO_TEST,
}
NODE_TEST = "const test = require('node:test');\nconst assert = require('node:assert');\n"


def bugfix_state(project, *, package=GREET):
    settings = {"regression": {"python": sys.executable}, **({"package": package} if package else {})}
    return {
        "base_commit": project.base,
        "settings": settings,
        "iteration": 1,
        "stages": [],
        "history": [],
        "goal_contract": {"body": {"task_kind": "bugfix", "acceptance_criteria": [], "milestones": [{"id": "M1"}]}},
    }


class PackageNames(unittest.TestCase):
    def test_a_package_is_a_plain_directory_inside_the_repository(self):
        self.assertEqual("packages/api", package_scope.normalize("packages/api/"))
        self.assertEqual("api", package_scope.normalize("api"))
        for value in ("", ".", "./api", "../api", "/srv/api", "packages/../api", "packages//api", "api/*", "a b"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "--package must be a directory"):
                package_scope.normalize(value)

    def test_a_packaged_command_enters_the_package_and_nothing_else_does(self):
        packaged = package_scope.command(GREET, "go test ./...")
        self.assertEqual("cd packages/greet && go test ./...", packaged)
        self.assertEqual("go test ./...", package_scope.inner(GREET, packaged))
        self.assertIsNone(package_scope.inner(OTHER, packaged))
        self.assertIsNone(package_scope.inner(GREET, "go test ./..."))
        self.assertIsNone(package_scope.inner(GREET, "cd packages/greet && "))
        # A saved command (the protected replay's) is read back only in exactly the form command() writes.
        self.assertEqual((GREET, "go test ./..."), package_scope.entered(packaged))
        for text in ("go test ./...", "cd packages/greet/ && go test", "cd 'a b' && go test", "cd ../x && go test"):
            with self.subTest(text=text):
                self.assertIsNone(package_scope.entered(text))
        self.assertIsNone(package_scope.entered("cd packages/greet && "))
        # Per-test ids are relative to where their command ran.
        self.assertEqual(Path("/repo") / GREET, package_scope.ids_root("/repo", GREET, packaged))
        self.assertEqual(Path("/repo"), package_scope.ids_root("/repo", GREET, "go test ./..."))
        self.assertEqual(Path("/repo"), package_scope.ids_root("/repo", None, packaged))
        self.assertEqual({}, package_scope.detection({}))
        self.assertEqual({}, package_scope.detection(None))
        self.assertEqual({"package": GREET}, package_scope.detection({"package": GREET}))
        self.assertEqual(GREET, run_view.view({"status": "RUNNING", "settings": {"package": GREET}})["package"])
        self.assertIsNone(run_view.view({"status": "RUNNING", "settings": {}})["package"])

    def test_a_sibling_whose_name_starts_with_the_package_name_is_outside_it(self):
        web, admin = "packages/web", "packages/web-admin/test_admin.py"
        self.assertTrue(package_scope.contains(web, "packages/web/test_web.py"))
        for path in (admin, "packages/web", "packages/webapp.py"):
            with self.subTest(path=path):
                self.assertFalse(package_scope.contains(web, path))
        self.assertEqual([admin], package_scope.outside(web, [admin, "packages/web/test_web.py"]))

    def test_a_report_may_name_the_package_directory_and_plain_paths_inside_it(self):
        # A report's affected and test paths (#711).
        inside = ["packages/web", "packages/web/", "./packages/web/app.py", "packages/web/tests/"]
        outside = ["packages/web-admin/app.py", "tests/test_app.py", "package.json"]
        self.assertEqual(sorted(outside), package_scope.named_outside("packages/web", [*inside, *outside]))
        self.assertEqual([], package_scope.named_outside("packages/web", inside))

    def test_a_change_outside_the_package_is_any_changed_path_not_strictly_inside_it(self):
        changes = {
            f"{GREET}/greet.py": "modified",
            f"{GREET}/sub/new.py": "added",
            f"{OTHER}/other.py": "deleted",
            "packages/greeter/x.py": "added",
            GREET: "added",
            "README.md": "modified",
        }
        outside = {
            GREET: "added",
            "README.md": "modified",
            "packages/greeter/x.py": "added",
            f"{OTHER}/other.py": "deleted",
        }
        self.assertEqual(outside, package_scope.changes_outside(GREET, changes))
        self.assertEqual(sorted(outside), list(package_scope.changes_outside(GREET, changes)))
        self.assertEqual({}, package_scope.changes_outside(GREET, {f"{GREET}/greet.py": "deleted"}))

    def test_an_exempt_path_is_left_out_with_everything_under_it(self):
        changes = {
            "vendored/": "added",  # a nested repository, as changed_files reports one
            "vendored//x.txt": "added",  # or its files, read through declared source paths
            "libs/lib": "modified",  # a submodule with uncommitted work
            "libs/lib/src.c": "added",
            "libs/library.c": "modified",  # beside the submodule, not under it
            "docs/bugs/note.json": "added",
            "README.md": "modified",
        }
        exempt = ["vendored/", "libs/lib", "docs/bugs/note.json", ""]
        self.assertEqual(
            {"README.md": "modified", "libs/library.c": "modified"},
            package_scope.changes_outside(GREET, changes, exempt),
        )

    def test_proving_tests_are_narrowed_inside_the_package(self):
        command = package_scope.command(GREET, f"{sys.executable} -m unittest -v test_greet.py")
        narrowed = proof_stability.narrow(
            "unittest", command, ["test_greet.TestGreet.test_blank_name_rejected"], directory=GREET
        )
        self.assertEqual(
            package_scope.command(
                GREET, f"{sys.executable} -m unittest -v test_greet.TestGreet.test_blank_name_rejected"
            ),
            narrowed,
        )
        # A command that does not enter the package is narrowed as before: never, when it is a shell list.
        self.assertIsNone(proof_stability.narrow("unittest", command, ["test_greet.TestGreet.test_ada"]))


class PackageSuite(unittest.TestCase):
    def setUp(self):
        self.project = Project(MONOREPO)
        self.addCleanup(self.project.close)
        temp = tempfile.TemporaryDirectory(prefix="package-scope-")
        self.addCleanup(temp.cleanup)
        self.run_dir = Path(temp.name)

    def test_the_package_suite_is_detected_there_and_runs_from_its_directory(self):
        framework = verify.detect_framework(self.project.root, python=sys.executable, package=GREET)
        self.assertEqual("unittest", framework.name)
        self.assertEqual(package_scope.command(GREET, f"{sys.executable} -m unittest discover -v"), framework.suite)
        self.assertEqual(GREET, framework.to_dict()["directory"])
        self.assertEqual(
            package_scope.command(GREET, f"{sys.executable} -m unittest -v test_greet.py"),
            framework.targeted([f"{GREET}/test_greet.py", f"{OTHER}/test_other.py"]),
        )
        self.assertIsNone(framework.targeted([f"{OTHER}/test_other.py"]))
        receipt = verify.run_suite(framework, framework.suite, self.project.root, self.run_dir, "suite", timeout=120)
        self.assertEqual(0, receipt["exit_code"], Path(receipt["output"]).read_text())
        self.assertTrue(receipt["command"].startswith("cd packages/greet && "), receipt["command"])
        self.assertTrue(receipt["results_expected"])
        self.assertEqual(SEED_TESTS, sorted(receipt["results"]["passed"]))
        # The other package's tests are not this package's suite.
        self.assertFalse([test for test in receipt["results"]["passed"] if "other" in test])

    def test_the_candidate_changes_outside_the_package_count_each_side_of_a_rename_and_no_autocode_file(self):
        root = self.project.root
        (root / OTHER / "other.py").rename(root / GREET / "other.py")  # into the package: its old path is outside
        (root / GREET / "greet.py").rename(root / "greet.py")  # out of it: its new path is outside
        (root / OTHER / "test_other.py").unlink()
        self.project.write(
            {
                ".autocode/runs/run/state.json": "{}\n",
                ".autocode-ui/session.json": "{}\n",
                f"{GREET}/__pycache__/greet.cpython-313.pyc": "bytecode",
            }
        )
        (root / "node_modules").symlink_to(root / OTHER, target_is_directory=True)  # the runner's dependency link
        changes = verify.changed_files(root, self.project.base)
        self.assertEqual(
            {"greet.py": "added", f"{OTHER}/other.py": "deleted", f"{OTHER}/test_other.py": "deleted"},
            package_scope.changes_outside(GREET, changes),
        )
        self.assertEqual(
            {f"{GREET}/greet.py": "deleted", f"{GREET}/other.py": "added"},
            {path: change for path, change in changes.items() if package_scope.contains(GREET, path)},
        )

    def test_a_package_that_is_missing_or_a_symlink_has_no_suite(self):
        self.assertIsNone(verify.detect_framework(self.project.root, python=sys.executable, package="packages/none"))
        (self.project.root / "linked").symlink_to(self.project.root / GREET, target_is_directory=True)
        self.assertIsNone(verify.detect_framework(self.project.root, python=sys.executable, package="linked"))

    def test_a_node_package_suite_is_its_own_test_script(self):
        project = Project(
            {
                **MONOREPO,
                "packages/web/package.json": '{"name": "web", "scripts": {"test": "node --test"}}\n',
                "packages/web/sum.test.js": "require('node:test')('sums', () => {});\n",
            }
        )
        self.addCleanup(project.close)
        framework = verify.detect_framework(project.root, python=sys.executable, package="packages/web")
        self.assertEqual("node", framework.name)
        self.assertEqual("cd packages/web && npm test --silent", framework.suite)

    def test_a_go_package_reports_each_test_from_its_own_module(self):
        if shutil.which("go") is None:
            self.skipTest("go is not installed")
        project = Project({**MONOREPO, **GO_PACKAGE})
        self.addCleanup(project.close)
        framework = verify.detect_framework(project.root, python=sys.executable, package="packages/svc")
        self.assertEqual(("go", "cd packages/svc && go test ./..."), (framework.name, framework.suite))
        receipt = verify.run_suite(framework, framework.suite, project.root, self.run_dir, "go", timeout=300)
        self.assertEqual(0, receipt["exit_code"], Path(receipt["output"]).read_text())
        self.assertEqual("cd packages/svc && go test -json ./...", receipt["command"])
        self.assertEqual(["example.test/svc::TestDouble"], receipt["results"]["passed"])

    def test_the_protected_inventory_holds_only_the_package_tests(self):
        everything = protected_oracles.inventory(self.project.root, verify.is_test_path)
        self.assertEqual({f"{GREET}/test_greet.py", f"{OTHER}/test_other.py"}, set(everything))
        scoped = protected_oracles.inventory(self.project.root, verify.is_test_path, package=GREET)
        self.assertEqual({f"{GREET}/test_greet.py": everything[f"{GREET}/test_greet.py"]}, scoped)

    def test_the_protected_inventory_leaves_out_a_sibling_whose_name_starts_with_the_package_name(self):
        test = "import unittest\n"
        project = Project({"packages/web/test_web.py": test, "packages/web-admin/test_admin.py": test})
        self.addCleanup(project.close)
        scoped = protected_oracles.inventory(project.root, verify.is_test_path, package="packages/web")
        self.assertEqual(["packages/web/test_web.py"], sorted(scoped))

    def test_a_direct_node_suite_always_runs_fresh_in_a_package_as_at_the_root(self):
        # Direct node:test runs are never reused from a cache: preloads and module resolution are not bound.
        project = Project({**MONOREPO, "packages/web/sum.test.js": "require('node:test')('sums', () => {});\n"})
        self.addCleanup(project.close)
        framework = verify.detect_framework(project.root, python=sys.executable, package="packages/web")
        self.assertEqual(("node", "cd packages/web && node --test sum.test.js"), (framework.name, framework.suite))
        for command in ("node --test sum.test.js", framework.suite):
            with self.subTest(command=command):
                identity = verify.baseline_identity(project.root, command=command)
                self.assertEqual("fresh_execution_only", identity["cache_policy"])
                self.assertFalse(identity["cache_binding_complete"])
                self.assertFalse(identity["reuse_supported"])

    def preflight(self, project, settings):
        """The named-proof preflight's refusal of the run's commands, or "" when it accepts them.

        A saved manifest holds one named-proof check; task_preflight.guard judges the commands before it
        runs any check. The check's own execution is replaced here: reaching it means the commands passed.
        """
        manifest = self.run_dir / "preflight.json"
        contract = {
            "kind": "proof",
            "test_changes_allowed": True,
            "cases": [{"id": "C1", "selector": "test_svc.T.test_c1", "kind": "fail_to_pass"}],
        }
        check = {"id": "controls", "phase": "planning", "argv": ["{python}", "controls.py"], "execution": "runner"}
        check.update(reuse=False, recovery="Restore the approved controls and resume", contract=contract)
        manifest.write_text(json.dumps({"version": 2, "inputs": [], "runtime_files": [], "checks": [check]}))
        settings = {**settings, "task_preflight": task_preflight.load(manifest)}
        state = {"status": "RUNNING", "next_stage": "requirements_gather", "settings": settings}
        with (
            patch.object(preflight_worker, "run", side_effect=RuntimeError("the check ran")) as run,
            self.assertRaises(util.Paused) as paused,
        ):
            task_preflight.guard(state, project.root, self.run_dir)
        message = paused.exception.args[-1]
        refused = "cannot supply the named results" in message
        self.assertEqual(not refused, run.called, message)
        return message if refused else ""

    def test_preflight_and_native_test_names_choose_the_package_suite(self):
        project = Project({**MONOREPO, **GO_PACKAGE})
        self.addCleanup(project.close)
        root_settings = {"regression": {"python": sys.executable}}
        settings = {**root_settings, "package": "packages/svc"}
        # The Go-specific native test names apply only when the proof will run Go.
        self.assertEqual("go", native_names.proof_framework({"workspace": str(project.root), "settings": settings}))
        self.assertEqual(
            "unittest", native_names.proof_framework({"workspace": str(project.root), "settings": root_settings})
        )
        # A named-proof check needs a command that reports each test; the package's Go suite does.
        packaged = {"test_command": "cd packages/svc && go test ./...", "python": sys.executable}
        self.assertEqual("", self.preflight(project, {**settings, "regression": packaged}))
        for refused in (
            {**root_settings, "regression": packaged},
            {**settings, "regression": {**packaged, "test_command": "cd packages/svc && go vet ./..."}},
        ):
            with self.subTest(settings=refused):
                self.assertIn("cannot supply the named results", self.preflight(project, refused))

    def test_an_explicit_pytest_command_for_the_package_chooses_the_proof_runner(self):
        # Pytest configured only at the repository root is not read by detection in the package, which finds
        # unittest. The preflight and the native test names read a pytest command written for the package with
        # pytest, as the proof does. This test runs no pytest; the Go test below runs the proof.
        explicit = {"python": sys.executable, "test_command": "cd packages/greet && python3 -m pytest -q"}
        settings = {"regression": explicit, "package": GREET}
        state = {"workspace": str(self.project.root), "settings": settings}
        self.assertEqual("pytest", native_names.proof_framework(state))
        self.assertEqual("", self.preflight(self.project, settings))
        # The same command is not the run's own in a run of another package or of the whole repository.
        for other in ({**settings, "package": OTHER}, {"regression": explicit}):
            with self.subTest(package=other.get("package")):
                state = {"workspace": str(self.project.root), "settings": other}
                self.assertEqual("unittest", native_names.proof_framework(state))
                self.assertIn("cannot supply the named results", self.preflight(self.project, other))

    @unittest.skipUnless(shutil.which("go"), "go test runs the package")
    def test_a_package_of_a_root_go_module_is_proven_with_a_command_written_for_it(self):
        # Detection finds nothing in the package, so the person writes the command for it; the proof then names
        # the failing test from go test's own results inside the package, and the preflight accepts the command.
        project = Project(GO_ROOT_MODULE)
        self.addCleanup(project.close)
        self.assertIsNone(verify.detect_framework(project.root, python=sys.executable, package="packages/svc"))
        double = "\nfunc TestDouble(t *testing.T) {\n\tif Double(2) != 4 {\n\t\tt.Fatal(Double(2))\n\t}\n}\n"
        project.write(
            {
                "packages/svc/svc.go": GO_PACKAGE["packages/svc/svc.go"],
                "packages/svc/svc_test.go": GO_ZERO_TEST + double,
            }
        )
        state = bugfix_state(project, package="packages/svc")
        state["workspace"] = str(project.root)
        state["settings"]["regression"]["test_command"] = "cd packages/svc && go test ./..."
        self.assertEqual("go", native_names.proof_framework(state))
        self.assertEqual("", self.preflight(project, state["settings"]))
        proof = regression.prove(state, project.root, self.run_dir)
        self.assertEqual(verify.PASS, proof["verdict"], proof["failures"] + proof["unverified"])
        self.assertEqual(["example.test/mono/packages/svc::TestDouble"], proof["fail_to_pass"])
        self.assertEqual("cd packages/svc && go test .", proof["commands"]["regression"])

    @unittest.skipUnless(shutil.which("node") and shutil.which("npm"), "npm runs the package's test script")
    def test_a_vitest_package_names_each_test_relative_to_the_package(self):
        # A stand-in for Vitest 4 that writes the runner's reporter file for the test file in its working directory.
        fake = (
            "import { writeFileSync } from 'node:fs';\nimport { resolve } from 'node:path';\n"
            "const out = process.argv.find((arg) => arg.startsWith('--outputFile=')).slice(13);\n"
            "const tests = [{ name: 'test_sums', state: 'passed', collection_error: false }];\n"
            "writeFileSync(out, JSON.stringify({ protocol: 'autocode-vitest-tests', version: 1, "
            "vitest_version: '4.1.6', complete: true, reason: 'passed', unhandled_errors: 0, total: 1, "
            "files: [{ file: resolve('sum.test.js'), collection_error: false, tests }] }));\n"
        )
        package = {"name": "web", "scripts": {"test": "node node_modules/vitest/vitest.mjs run"}}
        project = Project(
            {
                **MONOREPO,
                "packages/web/package.json": json.dumps({**package, "devDependencies": {"vitest": "4.1.6"}}),
                "packages/web/node_modules/vitest/vitest.mjs": fake,
                "packages/web/sum.test.js": "test('test_sums', () => {});\n",
            }
        )
        self.addCleanup(project.close)
        framework = verify.detect_framework(project.root, python=sys.executable, package="packages/web")
        self.assertEqual(("vitest", "cd packages/web && npm test --silent"), (framework.name, framework.suite))
        receipt = verify.run_suite(framework, framework.suite, project.root, self.run_dir, "vitest", timeout=120)
        self.assertEqual(0, receipt["exit_code"], Path(receipt["output"]).read_text())
        self.assertTrue(receipt["command"].startswith("cd packages/web && npm test --silent -- "), receipt["command"])
        self.assertTrue(receipt["results_expected"])
        self.assertEqual(["sum.test.js::test_sums"], receipt["results"]["passed"])

    def test_a_protected_test_revision_names_exactly_the_package_tests(self):
        root, settings = self.project.root, {"package": GREET}

        def reconcile(state, args):
            return protected_oracles.reconcile(
                state,
                settings,
                args,
                root,
                self.run_dir,
                is_test_path=verify.is_test_path,
                discover_command=lambda: "cd packages/greet && python3 -m unittest discover -v",
            )

        reconcile({}, SimpleNamespace(run_dir=None, revise_protected_tests=None))
        original = settings["protected_tests"]
        self.assertEqual([f"{GREET}/test_greet.py"], sorted(original["files"]))
        self.project.write({f"{GREET}/test_greet.py": references.BUGFIX_REFERENCE["test_greet.py"]})
        paused = {"status": "PAUSED_VALIDATION", "next_stage": "tester"}
        proposal = self.run_dir / "revision.json"
        for files, accepted in (
            (protected_oracles.inventory(root, verify.is_test_path), False),
            (protected_oracles.inventory(root, verify.is_test_path, package=GREET), True),
        ):
            with self.subTest(files=sorted(files)):
                proposal.write_text(
                    json.dumps(
                        {
                            "previous_hash": original["binding_hash"],
                            "files": files,
                            "command": original["command"],
                            "reason": "The person accepts the new greeting test",
                        }
                    )
                )
                args = SimpleNamespace(run_dir=self.run_dir, resume_paused=True, revise_protected_tests=proposal)
                if not accepted:
                    with self.assertRaisesRegex(ValueError, "must identify every current eligible test"):
                        reconcile(paused, args)
                    continue
                reconcile(paused, args)
                self.assertEqual(files, settings["protected_tests"]["files"])
                self.assertEqual([f"{GREET}/test_greet.py"], sorted(files))

    def replay(self, project, package, candidate):
        """The protected-test command and the replay receipt for ``candidate``, which must be refused."""
        settings = {"package": package} if package else {}
        framework = verify.detect_framework(project.root, python=sys.executable, **package_scope.detection(settings))
        protected_oracles.reconcile(
            {},
            settings,
            SimpleNamespace(run_dir=None, revise_protected_tests=None),
            project.root,
            self.run_dir,
            is_test_path=verify.is_test_path,
            discover_command=lambda: framework.suite,
        )
        project.write(candidate)
        with self.assertRaisesRegex(ValueError, "original gate did not pass") as refused:
            protected_oracles.replay(
                {"settings": settings}, project.root, self.run_dir, verify.scratch_run, timeout=300
            )
        receipt = re.search(r"Receipt: (\S+\.json)", str(refused.exception))
        return settings["protected_tests"]["command"], util.read(receipt[1])

    def test_a_package_replay_where_every_original_test_skips_is_refused_as_at_the_root(self):
        test = "import unittest\n\nfrom calc import double\n\n\nclass TestCalc(unittest.TestCase):\n"
        test += "    def test_double(self):\n        self.assertEqual(double(2), 4)\n"
        skip = "import unittest\n\nraise unittest.SkipTest('product disabled')\n"
        for prefix in ("", "packages/calc/"):
            with self.subTest(package=prefix or None):
                project = Project(
                    {f"{prefix}calc.py": "def double(n):\n    return 2 * n\n", f"{prefix}test_calc.py": test}
                )
                self.addCleanup(project.close)
                # The candidate rewords the protected test, and its product makes every test skip.
                candidate = {f"{prefix}calc.py": skip, f"{prefix}test_calc.py": test + "# reworded\n"}
                command, receipt = self.replay(project, prefix.strip("/"), candidate)
                self.assertEqual(bool(prefix), command.startswith("cd packages/calc && "), command)
                for side in ("candidate", "original"):
                    self.assertEqual(0, receipt[side]["exit_code"])
                    self.assertEqual("Test command reported only skipped tests", receipt[side]["error"])

    def test_a_package_replay_where_go_reports_no_test_is_refused(self):
        if shutil.which("go") is None:
            self.skipTest("go is not installed")
        test = 'package svc\n\nimport "testing"\n\nfunc TestDouble(t *testing.T) {\n\tif Double(2) != %d {\n'
        test += "\t\tt.Fatal(Double(2))\n\t}\n}\n"
        project = Project(
            {
                "packages/svc/go.mod": "module example.test/svc\n\ngo 1.21\n",
                "packages/svc/svc.go": "package svc\n\nfunc Double(n int) int { return 2 * n }\n",
                "packages/svc/svc_test.go": test % 4,
            }
        )
        self.addCleanup(project.close)
        # A weakened protected test, a broken product, and a new TestMain that runs no test at all.
        command, receipt = self.replay(
            project,
            "packages/svc",
            {
                "packages/svc/svc.go": "package svc\n\nfunc Double(n int) int { return 3 * n }\n",
                "packages/svc/svc_test.go": test % 6,
                "packages/svc/main_test.go": 'package svc\n\nimport (\n\t"os"\n\t"testing"\n)\n\n'
                "func TestMain(m *testing.M) { os.Exit(0) }\n",
            },
        )
        self.assertEqual("cd packages/svc && go test ./...", command)
        for side in ("candidate", "original"):
            self.assertEqual(0, receipt[side]["exit_code"])
            self.assertEqual("Test command reported zero tests or incomplete per-test results", receipt[side]["error"])

    @unittest.skipUnless(shutil.which("node") and shutil.which("npm"), "Node and npm run the package suite")
    def test_a_package_node_test_file_that_spawns_another_runner_proves_no_case(self):
        # #380 inside a package: its node:test ids name files relative to the package, where the suite ran.
        header = "const test = require('node:test');\nconst assert = require('node:assert');\n"
        project = Project(
            {
                "packages/web/package.json": '{"name": "web", "scripts": {"test": "node --test"}}\n',
                "packages/web/sum.js": "exports.sum = (a, b) => a - b;\n",
                "packages/web/sum.test.js": header + "test('zero', () => { assert.strictEqual(0, 0); });\n",
            }
        )
        self.addCleanup(project.close)
        project.write(
            {
                "packages/web/sum.js": "exports.sum = (a, b) => a + b;\n",
                "packages/web/sum.test.js": header
                + "const { execSync } = require('child_process');\nconst { sum } = require('./sum');\n"
                "test('test_sums', () => { assert.strictEqual(sum(1, 2), 3); });\n"
                "test('wrapped', { skip: true }, () => { execSync('npx vitest run -t test_sums'); });\n",
            }
        )
        state = bugfix_state(project, package="packages/web")
        state["goal_contract"]["body"]["acceptance_criteria"] = [
            {"id": "C1", "criterion": "Two numbers are summed", "verification_method": "test: test_sums"}
        ]
        proof = regression.prove(state, project.root, self.run_dir)
        self.assertEqual(["sum.test.js::test_sums"], proof["fail_to_pass"])
        self.assertEqual(verify.FAIL, proof["verdict"], proof)
        self.assertEqual([], proof["case_tests"]["C1"])
        self.assertIn("sum.test.js runs another test runner through child_process", " ".join(proof["failures"]))

    @unittest.skipUnless(shutil.which("node"), "node runs the package suite")
    def test_an_unchanged_package_test_that_spawns_another_runner_proves_no_guard_case(self):
        # #380 for the suite's own tests: a guard names an unchanged test of the package's direct node:test
        # suite, whose id is relative to the package. Its file spawns another runner, so it proves nothing.
        wrapper = (
            NODE_TEST + "const { execSync } = require('child_process');\n"
            "test('test_sums', () => { assert.ok(true); });\n"
            "test('wrapped', { skip: true }, () => { execSync('npx vitest run -t test_sums'); });\n"
        )
        zero = "test('zero', () => { assert.strictEqual(0, 0); });\n"
        project = Project(
            {
                "packages/web/sum.js": "exports.sum = (a, b) => a - b;\n",
                "packages/web/sum.test.js": wrapper,
                "packages/web/fix.test.js": NODE_TEST + zero,
            }
        )
        self.addCleanup(project.close)
        adds = "const { sum } = require('./sum');\ntest('test_adds', () => { assert.strictEqual(sum(1, 2), 3); });\n"
        project.write(
            {
                "packages/web/sum.js": "exports.sum = (a, b) => a + b;\n",
                "packages/web/fix.test.js": NODE_TEST + zero + adds,
            }
        )
        state = bugfix_state(project, package="packages/web")
        state["goal_contract"]["body"].update(
            task_kind="feature",
            acceptance_criteria=[
                {"id": "C1", "criterion": "Two numbers are added", "verification_method": "test: test_adds"},
                {"id": "C2", "criterion": "Sums still work", "verification_method": "guard: test_sums"},
            ],
        )
        proof = regression.prove(state, project.root, self.run_dir)
        self.assertEqual("cd packages/web && node --test fix.test.js sum.test.js", proof["commands"]["suite"])
        self.assertEqual(verify.FAIL, proof["verdict"], proof)
        self.assertEqual({"C1": ["fix.test.js::test_adds"], "C2": []}, proof["case_tests"])
        failure = " ".join(proof["failures"])
        self.assertIn("Preserve case C2", failure)
        self.assertIn("sum.test.js runs another test runner through child_process", failure)

    def test_a_package_bug_fix_is_proven_with_the_package_suite(self):
        self.project.write(FIX)
        proof = regression.prove(bugfix_state(self.project), self.project.root, self.run_dir)
        self.assertEqual(verify.PASS, proof["verdict"], proof["failures"] + proof["unverified"])
        self.assertEqual(["test_greet.TestGreet.test_blank_name_rejected"], proof["fail_to_pass"])
        self.assertTrue(proof["commands"]["suite"].startswith("cd packages/greet && "), proof["commands"])
        self.assertTrue(proof["commands"]["regression"].startswith("cd packages/greet && "), proof["commands"])

    def test_the_proving_test_is_repeated_inside_the_package(self):
        self.project.write(FIX)
        state = bugfix_state(self.project)
        state["goal_contract"]["body"]["acceptance_criteria"] = [
            {
                "id": "C1",
                "criterion": "A blank name is refused",
                "verification_method": "test: test_blank_name_rejected",
            }
        ]
        proof = regression.prove(state, self.project.root, self.run_dir)
        self.assertEqual(verify.PASS, proof["verdict"], proof["failures"] + proof["unverified"])
        self.assertEqual({"C1": ["test_greet.TestGreet.test_blank_name_rejected"]}, proof["case_tests"])
        self.assertEqual((None, []), (proof["proof_stability"]["note"], proof["proof_stability"]["unstable"]))
        checks = util.read(proof["path"])["checks"]
        for label in ("stability-candidate-3", "stability-base-3"):
            self.assertEqual(
                package_scope.command(
                    GREET, f"{sys.executable} -m unittest -v test_greet.TestGreet.test_blank_name_rejected"
                ),
                checks[label]["command"],
            )

    def test_without_the_package_the_same_fix_cannot_be_proven(self):
        # The motivation, kept as a control: from the repository root the package's tests cannot find greet.py.
        self.project.write(FIX)
        proof = regression.prove(bugfix_state(self.project, package=None), self.project.root, self.run_dir)
        self.assertNotEqual(verify.PASS, proof["verdict"])

    def test_a_change_outside_the_package_leaves_the_proof_unverified(self):
        self.project.write({**FIX, f"{OTHER}/other.py": "def answer():\n    return 41 + 1\n"})
        proof = regression.prove(bugfix_state(self.project), self.project.root, self.run_dir)
        self.assertEqual(verify.UNVERIFIED, proof["verdict"])
        self.assertEqual(["test_greet.TestGreet.test_blank_name_rejected"], proof["fail_to_pass"])
        self.assertIn(
            "Files outside the package packages/greet/ changed, and its suite does not cover them: "
            "packages/other/other.py",
            proof["unverified"],
        )


# A Node package inside a Python repository whose test script builds before it tests, like
# vscode-extension/ in this repository (2026-11-11: the Tester's tests there were handed to the root's
# pytest, which cannot run them). The build step copies src/ into out/, which the tests load.
WEB = "packages/web"
BUILD = "#!/bin/sh\nmkdir -p out && cp src/greet.js out/greet.js\n"
NESTED = {
    "pyproject.toml": "[tool.pytest.ini_options]\n",
    "tests/test_root.py": "def test_root():\n    pass\n",
    f"{WEB}/package.json": '{"name": "web", "scripts": {"test": "build-it && node --test test/*.test.cjs"}}\n',
    f"{WEB}/.gitignore": "node_modules/\nout/\n",
    f"{WEB}/src/greet.js": "exports.greet = () => 'hi';\n",
    f"{WEB}/test/old.test.cjs": "require('node:test')('test_old', () => {});\n",
}
NESTED_TEST = (
    "const test = require('node:test');\nconst assert = require('node:assert/strict');\n"
    "test('test_greets', () => { assert.equal(require('../out/greet.js').greet(), 'hello'); });\n"
    "test('guard_loads', () => { assert.equal(typeof require('../out/greet.js').greet, 'function'); });\n"
)

# The root's test reads helper.py, and the package's own test reads its built greet.js: code only one suite runs.
ROOT_HELPER = {
    "helper.py": "VALUE = 1\n",
    "tests/test_root.py": "import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parents[1]))\n"
    "import helper\n\ndef test_root():\n    assert helper.VALUE == 1\n",
}
OLD_WEB_TEST = (
    "const test = require('node:test');\nconst assert = require('node:assert/strict');\n"
    "test('test_old', () => { assert.equal(require('../out/greet.js').greet(), 'hi'); });\n"
)


def nested_project(case, extra=None):
    """The repository with its package's installed build tool, which Git ignores as npm's would be."""
    project = Project({**NESTED, **(extra or {})})
    case.addCleanup(project.close)
    tool = project.root / WEB / "node_modules/.bin/build-it"
    tool.parent.mkdir(parents=True)
    tool.write_text(BUILD)
    tool.chmod(0o755)
    return project


class NestedPackage(unittest.TestCase):
    """A run of the whole repository proves a package's tests with that package's own suite."""

    def test_the_package_around_the_tests_supplies_their_suite_with_its_build_step(self):
        project = nested_project(self)
        detect = lambda directory: verify.detect_framework(directory, python=sys.executable)  # noqa: E731
        root = detect(project.root)
        self.assertEqual("pytest", root.name)
        tests = [f"{WEB}/test/greet.test.cjs"]
        self.assertIsNone(root.targeted(tests), "the root's pytest cannot run a Node test file")
        found = package_scope.for_tests(project.root, root, tests, detect)
        self.assertEqual((WEB, "node"), (found.directory, found.name))
        self.assertEqual(f"cd {WEB} && npm test --silent", found.suite)
        self.assertEqual(f"cd {WEB} && node --test test/greet.test.cjs", found.targeted(tests))
        self.assertEqual("npm exec --call build-it", found.setup)
        self.assertEqual("npm exec --call build-it", found.to_dict()["setup"])
        # Tests the root suite runs keep it; no package holds tests in two packages or at the root.
        self.assertIsNone(package_scope.for_tests(project.root, root, ["tests/test_root.py"], detect))
        self.assertIsNone(package_scope.for_tests(project.root, root, [], detect))
        for elsewhere in ([*tests, "tests/test_other.cjs"], ["test/root.test.cjs"]):
            with self.subTest(tests=elsewhere):
                self.assertIsNone(package_scope.for_tests(project.root, root, elsewhere, detect))
        # A test in a directory below the package belongs to the package around it.
        deeper = package_scope.for_tests(project.root, root, [f"{WEB}/test/unit/a.test.cjs"], detect)
        self.assertEqual(
            f"cd {WEB} && node --test test/unit/a.test.cjs", deeper.targeted([f"{WEB}/test/unit/a.test.cjs"])
        )

    def test_a_directory_without_a_manifest_or_a_suite_is_not_a_package(self):
        project = nested_project(self)
        project.write({"docs/notes/a.test.cjs": "require('node:test')('x', () => {});\n"})
        detect = lambda directory: verify.detect_framework(directory, python=sys.executable)  # noqa: E731
        self.assertIsNone(package_scope.enclosing(project.root, ["docs/notes/a.test.cjs"], detect))
        self.assertIsNone(package_scope.enclosing(project.root, [f"{WEB}/test/a.test.cjs"], lambda directory: None))

    @unittest.skipUnless(shutil.which("node") and shutil.which("npm"), "Node and npm run the package suite")
    def test_the_testers_tests_run_on_todays_code_with_the_package_build_and_its_node_modules(self):
        project = nested_project(self)
        project.write({f"{WEB}/test/greet.test.cjs": NESTED_TEST})
        with tempfile.TemporaryDirectory() as run_dir:
            receipt = verify.scratch_run(project.root, Path(run_dir), tests=[f"{WEB}/test/greet.test.cjs"])
        self.assertFalse(receipt.get("error"), receipt)
        self.assertTrue(receipt["command"].startswith(f"cd {WEB} && npm exec --call build-it && node "), receipt)
        self.assertEqual(["test/greet.test.cjs::guard_loads"], receipt["results"]["passed"])
        self.assertEqual(["test/greet.test.cjs::test_greets"], receipt["results"]["failed"])
        # The runner's link to the package's node_modules is never part of a change.
        (project.root / "packages/other").mkdir()
        (project.root / "packages/other/node_modules").symlink_to(project.root / WEB, target_is_directory=True)
        self.assertNotIn("packages/other/node_modules", verify.changed_files(project.root, project.base))

    @unittest.skipUnless(shutil.which("node") and shutil.which("npm"), "Node and npm run the package suite")
    def test_a_fix_in_the_package_is_proven_with_its_suite_in_a_run_of_the_whole_repository(self):
        project = nested_project(self)
        project.write(
            {
                f"{WEB}/test/greet.test.cjs": NESTED_TEST,
                f"{WEB}/src/greet.js": "exports.greet = () => 'hello';\n",
                "CHANGELOG.md": "- It says hello\n",  # not code: the package suite still proves the change
            }
        )
        state = bugfix_state(project, package=None)
        state["goal_contract"]["body"]["acceptance_criteria"] = [
            {"id": "C1", "criterion": "It says hello", "verification_method": "test: test_greets"}
        ]
        with tempfile.TemporaryDirectory() as run_dir:
            proof = regression.prove(state, project.root, Path(run_dir))
        self.assertEqual(["test/greet.test.cjs::test_greets"], proof["fail_to_pass"], proof)
        self.assertEqual(["test/greet.test.cjs::test_greets"], proof["case_tests"]["C1"], proof)
        self.assertEqual((verify.PASS, WEB), (proof["verdict"], proof["framework"]["directory"]), proof)

    @unittest.skipUnless(shutil.which("node") and shutil.which("npm"), "Node and npm run the package suite")
    def test_a_code_change_outside_the_package_is_checked_by_the_root_suite(self):
        project = nested_project(self)
        project.write(
            {
                f"{WEB}/test/greet.test.cjs": NESTED_TEST,
                f"{WEB}/src/greet.js": "exports.greet = () => 'hello';\n",
                "helper.py": "VALUE = 1\n",
            }
        )
        state = bugfix_state(project, package=None)
        state["goal_contract"]["body"]["acceptance_criteria"] = [
            {"id": "C1", "criterion": "It says hello", "verification_method": "test: test_greets"}
        ]
        with tempfile.TemporaryDirectory() as run_dir:
            proof = regression.prove(state, project.root, Path(run_dir))
        # The package's suite proves the fix; the root's own suite checks helper.py, which it never runs (#2889).
        self.assertEqual((verify.PASS, WEB), (proof["verdict"], proof["framework"]["directory"]), proof)
        root_suite = verify.detect_framework(project.root, python=sys.executable).suite
        self.assertIn(
            f"Code in the repository root changed and the proof's suite `cd {WEB} && npm test --silent` cannot run "
            f"its tests, so its own suite `{root_suite}` ran on the original code and with the change",
            proof["notes"],
        )
        for label in ("companion_suite_on_base:.", "companion_suite_on_candidate:."):
            self.assertTrue(proof["checks"][label]["command"].startswith(root_suite), proof["checks"][label])
            self.assertEqual(0, proof["checks"][label]["exit_code"])
        self.assertEqual(
            {".": root_suite}, {k: v["suite"] for k, v in proof["execution_context"]["companion_suites"].items()}
        )

    @unittest.skipUnless(shutil.which("node") and shutil.which("npm"), "Node and npm run the package suite")
    def test_a_change_that_breaks_the_root_suite_fails_the_proof_the_package_suite_passed(self):
        project = nested_project(self, extra=ROOT_HELPER)
        project.write(
            {
                f"{WEB}/test/greet.test.cjs": NESTED_TEST,
                f"{WEB}/src/greet.js": "exports.greet = () => 'hello';\n",
                "helper.py": "VALUE = 2\n",  # breaks tests/test_root.py, which only the root's suite runs
            }
        )
        state = bugfix_state(project, package=None)
        state["goal_contract"]["body"]["acceptance_criteria"] = [
            {"id": "C1", "criterion": "It says hello", "verification_method": "test: test_greets"}
        ]
        with tempfile.TemporaryDirectory() as run_dir:
            proof = regression.prove(state, project.root, Path(run_dir))
        self.assertEqual((verify.FAIL, WEB), (proof["verdict"], proof["framework"]["directory"]), proof)
        self.assertEqual(["test/greet.test.cjs::test_greets"], proof["fail_to_pass"], proof)
        root_suite = verify.detect_framework(project.root, python=sys.executable).suite
        self.assertEqual(
            [
                f"The repository root's suite `{root_suite}`: tests that pass on the original code do not pass with "
                "the change: tests.test_root::test_root"
            ],
            [text for text in proof["failures"] if text.startswith("The repository root's suite")],
        )

    @unittest.skipUnless(shutil.which("node") and shutil.which("npm"), "Node and npm run the package suite")
    def test_the_package_suite_protects_its_tests_when_the_root_suite_proves_the_fix(self):
        project = nested_project(self, extra={**ROOT_HELPER, f"{WEB}/test/old.test.cjs": OLD_WEB_TEST})
        project.write(
            {
                "helper.py": "VALUE = 2\n",
                "tests/test_root.py": "import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parents[1]))\n"
                "import helper\n\ndef test_root():\n    assert helper.VALUE == 2\n",
                f"{WEB}/src/greet.js": "exports.greet = () => 'hello';\n",  # breaks the package's own test
            }
        )
        state = bugfix_state(project, package=None)
        state["goal_contract"]["body"]["acceptance_criteria"] = [
            {"id": "C1", "criterion": "VALUE is 2", "verification_method": "test: test_root"}
        ]
        with tempfile.TemporaryDirectory() as run_dir:
            proof = regression.prove(state, project.root, Path(run_dir))
        self.assertIsNone(proof["framework"].get("directory"), "the root's suite runs the proving test")
        self.assertEqual(verify.FAIL, proof["verdict"], proof)
        # `npm test` reports an exit code, not per-test results: it passed on the original code and fails now.
        self.assertIn(
            f"The {WEB}/ suite `cd {WEB} && npm test --silent` passes on the original code and fails with the change "
            "(exit 1)",
            proof["failures"],
        )
        self.assertEqual(
            [f"companion_suite_on_base:{WEB}", f"companion_suite_on_candidate:{WEB}"],
            sorted(label for label in proof["checks"] if label.startswith("companion")),
        )

    @unittest.skipUnless(shutil.which("node") and shutil.which("npm"), "Node and npm run the package suite")
    def test_a_guard_on_a_root_test_is_credited_by_the_root_suite_when_the_package_proves_the_fix(self):
        # A live Kilo run (2026-11-11) proved its fix with one project's suite while a guard named a test of
        # the other: the proof looked for it only in its own suite and failed twice, then sent the Builder back.
        project = nested_project(self)
        project.write(
            {f"{WEB}/test/greet.test.cjs": NESTED_TEST, f"{WEB}/src/greet.js": "exports.greet = () => 'hello';\n"}
        )
        state = bugfix_state(project, package=None)
        state["goal_contract"]["body"]["acceptance_criteria"] = [
            {"id": "C1", "criterion": "It says hello", "verification_method": "test: test_greets"},
            {"id": "C2", "criterion": "The root still works", "verification_method": "guard: test_root"},
        ]
        with tempfile.TemporaryDirectory() as run_dir:
            proof = regression.prove(state, project.root, Path(run_dir))
        self.assertEqual((verify.PASS, WEB), (proof["verdict"], proof["framework"]["directory"]), proof)
        self.assertEqual(["tests.test_root::test_root"], proof["case_tests"]["C2"], proof)
        root = verify.detect_framework(project.root, python=sys.executable)
        command = root.targeted(["tests/test_root.py"])
        self.assertIn(
            f"The guards' tests in tests/test_root.py are in the repository root, whose tests the proof's suite "
            f"`cd {WEB} && npm test --silent` cannot run, so `{command}` ran them on the original code and with "
            "the change",
            proof["notes"],
        )
        for label in ("companion_guards_on_base:.", "companion_guards_on_candidate:."):
            self.assertTrue(proof["checks"][label]["command"].startswith(command), proof["checks"][label])
            self.assertEqual(0, proof["checks"][label]["exit_code"])
        self.assertEqual(
            {".": command}, {k: v["command"] for k, v in proof["execution_context"]["companion_guards"].items()}
        )

    @unittest.skipUnless(shutil.which("node") and shutil.which("npm"), "Node and npm run the package suite")
    def test_a_guard_on_a_package_test_is_credited_by_the_package_suite_when_the_root_proves_the_fix(self):
        project = nested_project(self, extra={**ROOT_HELPER, f"{WEB}/test/old.test.cjs": OLD_WEB_TEST})
        fix = {
            "helper.py": "VALUE = 2\n",
            "tests/test_root.py": "import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parents[1]))\n"
            "import helper\n\ndef test_root():\n    assert helper.VALUE == 2\n",
        }
        state = bugfix_state(project, package=None)
        state["goal_contract"]["body"]["acceptance_criteria"] = [
            {"id": "C1", "criterion": "VALUE is 2", "verification_method": "test: test_root"},
            {"id": "C2", "criterion": "The greeting still says hi", "verification_method": "guard: test_old"},
        ]
        project.write(fix)
        with tempfile.TemporaryDirectory() as run_dir:
            proof = regression.prove(state, project.root, Path(run_dir))
        self.assertIsNone(proof["framework"].get("directory"), "the root's suite runs the proving test")
        self.assertEqual(verify.PASS, proof["verdict"], proof)
        self.assertEqual(["test/old.test.cjs::test_old"], proof["case_tests"]["C2"], proof)
        self.assertEqual(
            [f"companion_guards_on_base:{WEB}", f"companion_guards_on_candidate:{WEB}"],
            sorted(label for label in proof["checks"] if label.startswith("companion")),
            "no code in the package changed: only the guard's own test file runs there",
        )
        # The guard's test failing with the change is never credited.
        project.write({f"{WEB}/src/greet.js": "exports.greet = () => 'hello';\n"})
        with tempfile.TemporaryDirectory() as run_dir:
            proof = regression.prove(state, project.root, Path(run_dir))
        self.assertEqual(verify.FAIL, proof["verdict"], proof)
        self.assertEqual([], proof["case_tests"]["C2"], proof)
        self.assertIn(
            "Preserve case C2: The greeting still says hi has no test named test_old that passes both with the "
            "change and on the original code",
            proof["failures"],
        )

    def test_enclosing_needs_tests_below_the_root(self):
        project = nested_project(self)
        detect = lambda directory: verify.detect_framework(directory, python=sys.executable)  # noqa: E731
        self.assertIsNone(package_scope.enclosing(project.root, [], detect))
        self.assertIsNone(package_scope.enclosing(project.root, ["a.test.cjs"], detect))
        self.assertEqual(WEB, package_scope.enclosing(project.root, [f"./{WEB}/test/a.test.cjs"], detect).directory)


# A polyglot repository like the one in #2889: a Node suite at the root (one script test under
# frontend/) and a Rails-style backend/ with its own Gemfile and RSpec specs. The plan's proving test
# is an RSpec file under backend/, which the root's Node suite can never run.
POLYGLOT = {
    "package.json": '{"name": "site", "scripts": {"test": "node --test frontend/scripts/routing.test.mjs"}}\n',
    "frontend/scripts/routing.mjs": "export const route = () => '/staging';\n",
    "frontend/scripts/routing.test.mjs": "import test from 'node:test';\nimport assert from 'node:assert';\n"
    "import { route } from './routing.mjs';\ntest('routes', () => assert.ok(route().startsWith('/')));\n",
    "backend/Gemfile": "source 'https://rubygems.org'\ngem 'rspec'\n",
    "backend/app/grader.rb": "class Grader\n  def grade\n    :failed\n  end\nend\n",
    "backend/spec/requests/existing_spec.rb": "RSpec.describe 'existing' do\n  it('runs') { expect(1).to eq(1) }\nend\n",
}
POLYGLOT_SPEC = "backend/spec/requests/grader_spec.rb"


SIDE_BY_SIDE = {
    "README.rst": "Two projects side by side, as in Docutils (#2944).\n",
    "proj/pyproject.toml": '[project]\nname = "proj"\n',
    "proj/calc.py": "def add(a, b):\n    return a + b\n",
    "proj/tests/test_calc.py": "import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parents[1]))\n"
    "import calc\n\ndef test_adds():\n    assert calc.add(1, 2) == 3\n",
    "sandbox/tests/test_old.py": 'print "a Python 2 experiment"\n',
}


class SideBySideProjects(unittest.TestCase):
    """A root with no manifest is no project of its own: tests inside a project are its own suite's (#2944)."""

    def detect(self, directory):
        return verify.detect_framework(directory, python=sys.executable)

    def project(self, extra=None):
        project = Project({**SIDE_BY_SIDE, **(extra or {})})
        self.addCleanup(project.close)
        return project

    def test_the_project_around_the_tests_supplies_the_suite_even_when_the_roots_could_run_them(self):
        project = self.project()
        root = self.detect(project.root)
        tests = ["proj/tests/test_calc.py"]
        self.assertTrue(root.targeted(tests), "the root's pytest collects the test, and sandbox/ with it")
        found = package_scope.for_tests(project.root, root, tests, self.detect)
        self.assertEqual(("proj", "pytest"), (found.directory, found.name))
        self.assertTrue(found.suite.startswith("cd proj && "), found.suite)
        self.assertIsNone(package_scope.for_tests(project.root, root, ["sandbox/tests/test_old.py"], self.detect))

    def test_a_root_with_its_own_manifest_keeps_its_suite(self):
        project = self.project({"pyproject.toml": '[project]\nname = "root"\n'})
        root = self.detect(project.root)
        self.assertIsNone(package_scope.for_tests(project.root, root, ["proj/tests/test_calc.py"], self.detect))

    def test_a_fix_inside_the_project_is_proven_without_the_neighbours_broken_tests(self):
        project = self.project()
        project.write(
            {
                "proj/calc.py": "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n",
                "proj/tests/test_calc.py": SIDE_BY_SIDE["proj/tests/test_calc.py"]
                + "\n\ndef test_subtracts():\n    assert calc.sub(3, 1) == 2\n",
            }
        )
        state = bugfix_state(project, package=None)
        state["goal_contract"]["body"]["acceptance_criteria"] = [
            {"id": "C1", "criterion": "It subtracts", "verification_method": "test: test_subtracts"}
        ]
        with tempfile.TemporaryDirectory() as run_dir:
            proof = regression.prove(state, project.root, Path(run_dir))
        self.assertEqual((verify.PASS, "proj"), (proof["verdict"], proof["framework"]["directory"]), proof)
        self.assertEqual(["tests.test_calc::test_subtracts"], proof["fail_to_pass"], proof)


class PolyglotSuite(unittest.TestCase):
    """A proving test the detected suite cannot hold is a selection problem, never 'passes on base' (#2889)."""

    def project(self):
        project = Project(POLYGLOT)
        self.addCleanup(project.close)
        return project

    def test_the_backend_directory_holding_the_gemfile_supplies_the_rspec_runner(self):
        project = self.project()
        detect = lambda directory: verify.detect_framework(directory, python=sys.executable)  # noqa: E731
        root = detect(project.root)
        self.assertEqual("node", root.name)
        self.assertIsNone(root.targeted([POLYGLOT_SPEC]), "the root's Node suite cannot run an RSpec file")
        found = package_scope.for_tests(project.root, root, [POLYGLOT_SPEC], detect)
        self.assertEqual(("backend", "rspec"), (found.directory, found.name))
        self.assertEqual(
            "cd backend && bundle exec rspec spec/requests/grader_spec.rb", found.targeted([POLYGLOT_SPEC])
        )

    @unittest.skipUnless(shutil.which("node"), "Node runs the root suite")
    def test_a_suite_that_cannot_run_the_proving_tests_reports_that_instead_of_passing_on_base(self):
        # Without backend/'s Gemfile no project around the spec has a suite, so the root's Node suite is kept.
        project = Project({path: text for path, text in POLYGLOT.items() if path != "backend/Gemfile"})
        self.addCleanup(project.close)
        project.write(
            {
                POLYGLOT_SPEC: "require_relative '../../app/grader'\nRSpec.describe Grader do\n"
                "  it('passes a correct answer') { expect(Grader.new.grade).to eq(:passed) }\nend\n",
                "backend/app/grader.rb": "class Grader\n  def grade\n    :passed\n  end\nend\n",
                # Code outside backend/ as well: the proof's one suite is still the root's.
                "frontend/scripts/routing.mjs": "export const route = () => '/staging/';\n",
            }
        )
        state = bugfix_state(project, package=None)
        state["goal_contract"]["body"]["acceptance_criteria"] = [
            {"id": "C1", "criterion": "A correct answer passes", "verification_method": "test: passes a correct answer"}
        ]
        with tempfile.TemporaryDirectory() as run_dir:
            proof = regression.prove(state, project.root, Path(run_dir))
        self.assertEqual("node", proof["framework"]["name"], proof)
        self.assertEqual(verify.UNVERIFIED, proof["verdict"], proof)
        self.assertNotIn(
            "The new tests pass on the unfixed base code, so they do not reproduce the bug", proof["failures"]
        )
        self.assertEqual(
            [
                "The proving tests are not in the suite that ran, so it cannot show whether they fail on the "
                f"original code: {POLYGLOT_SPEC} (the node suite `npm test --silent` cannot run them)"
            ],
            proof["unverified"],
        )
        self.assertNotIn("regression_on_base", proof["checks"], "a suite that cannot hold the tests proves nothing")

    @unittest.skipUnless(shutil.which("node"), "Node runs the root suite")
    def test_a_trusted_command_the_detected_runner_does_not_describe_still_decides_the_flip(self):
        project = self.project()
        project.write(
            {POLYGLOT_SPEC: "RSpec.describe 'x' do\n  it('y') { }\nend\n", "backend/app/grader.rb": "# fixed\n"}
        )
        state = bugfix_state(project, package=None)
        state["settings"]["regression"]["test_command"] = "cd backend && true"  # no runner AutoCode recognizes
        with tempfile.TemporaryDirectory() as run_dir:
            proof = regression.prove(state, project.root, Path(run_dir))
        self.assertEqual(("node", "explicit"), (proof["framework"]["name"], proof["commands"]["suite_source"]), proof)
        self.assertIn(
            "The new tests pass on the unfixed base code, so they do not reproduce the bug", proof["failures"]
        )

    @unittest.skipUnless(shutil.which("bundle") and shutil.which("rspec"), "Bundler and RSpec run backend/'s suite")
    def test_an_rspec_suite_without_a_junit_formatter_names_each_example(self):
        # The plan named RSpec examples and the project had no JUnit formatter: the proof saw only an exit code, so
        # every named criterion ended unverified (idlecampus, 2026-11-07). RSpec's own JSON formatter names them.
        existing = "RSpec.describe 'existing' do\n  it('test_existing_runs') { expect(1).to eq(1) }\nend\n"
        project = Project({**POLYGLOT, "backend/spec/requests/existing_spec.rb": existing})
        self.addCleanup(project.close)
        project.write(
            {
                POLYGLOT_SPEC: "require_relative '../../app/grader'\nRSpec.describe Grader do\n"
                "  it('test_passes_a_correct_answer') { expect(Grader.new.grade).to eq(:passed) }\nend\n",
                "backend/app/grader.rb": "class Grader\n  def grade\n    :passed\n  end\nend\n",
            }
        )
        state = bugfix_state(project, package=None)
        state["goal_contract"]["body"]["acceptance_criteria"] = [
            {
                "id": "C1",
                "criterion": "A correct answer passes",
                "verification_method": "test: test_passes_a_correct_answer",
            },
            {"id": "C2", "criterion": "The existing example runs", "verification_method": "guard: test_existing_runs"},
        ]
        with tempfile.TemporaryDirectory() as run_dir:
            proof = regression.prove(state, project.root, Path(run_dir))
        self.assertEqual(("rspec", "backend"), (proof["framework"]["name"], proof["framework"]["directory"]), proof)
        self.assertEqual(verify.PASS, proof["verdict"], proof)
        example = "spec/requests/grader_spec.rb::Grader test_passes_a_correct_answer"
        self.assertEqual([example], proof["fail_to_pass"], proof)
        self.assertEqual(
            {"C1": [example], "C2": ["spec/requests/existing_spec.rb::existing test_existing_runs"]},
            proof["case_tests"],
        )
        self.assertIn("--format json --out", proof["checks"]["regression_on_candidate"]["command"])


# The repository of a live Kilo bug fix (2026-11-12): a Node suite at the root and a Python project in backend/.
SPLIT = {
    "package.json": '{"name": "site", "scripts": {"test": "node --test frontend/routing.test.mjs"}}\n',
    "frontend/routing.mjs": "export const route = (p) => '/staging' + p;\n",
    "frontend/routing.test.mjs": "import test from 'node:test';\nimport assert from 'node:assert/strict';\n"
    "import { route } from './routing.mjs';\n"
    "test('test_route_adds_staging_prefix', () => assert.equal(route('/x'), '/staging/x'));\n",
    "backend/pyproject.toml": '[project]\nname = "backend"\nversion = "0.1.0"\n',
    "backend/grader.py": "def grade(answer, expected):\n    return 'passed' if answer == expected else 'failed'\n",
    "backend/tests/__init__.py": "",
}
SPLIT_TEST = (
    "import sys\nimport unittest\nfrom pathlib import Path\n\nsys.path.insert(0, str(Path(__file__).parents[1]))\n"
    "from grader import grade  # noqa: E402\n\n\nclass GraderTest(unittest.TestCase):\n"
    "    def test_ignores_case_and_spaces(self):\n        self.assertEqual('passed', grade(' Paris ', 'paris'))\n\n"
    "    def test_exact_match_passes(self):\n        self.assertEqual('passed', grade('a', 'a'))\n"
)
SPLIT_TESTS = ["backend/tests/test_grader.py", "frontend/routing.test.mjs"]


class TestsInTwoProjects(unittest.TestCase):
    """The Tester's tests run on today's code each with the suite of its own project (2026-11-12).

    A live run handed a backend/ Python test and a root Node test to the root's Node suite, which ran only the
    Node file. The runner told the Tester three times that its Python tests did not run, until it wrapped them
    in a Node test that shelled out to Python, edited a frontend test the plan said to leave alone, and paused."""

    def project(self, files=SPLIT):
        project = Project(files)
        self.addCleanup(project.close)
        project.write({"backend/tests/test_grader.py": SPLIT_TEST})
        return project

    def detect(self, directory):
        return verify.detect_framework(directory, python=sys.executable)

    @unittest.skipUnless(shutil.which("node"), "Node runs the root suite")
    def test_each_file_runs_with_the_suite_of_its_own_project_and_every_test_is_reported(self):
        project = self.project()
        with tempfile.TemporaryDirectory() as run_dir:
            receipt = verify.scratch_run(project.root, Path(run_dir), tests=SPLIT_TESTS)
        self.assertFalse(receipt.get("error"), receipt)
        results = receipt["results"]
        self.assertTrue(results["complete"], receipt)
        self.assertEqual(3, results["total"], results)
        passed = [test.rsplit(".", 1)[-1].rsplit("::", 1)[-1] for test in results["passed"]]
        failed = [test.rsplit(".", 1)[-1].rsplit("::", 1)[-1] for test in results["failed"]]
        self.assertEqual(["test_exact_match_passes", "test_route_adds_staging_prefix"], sorted(passed), results)
        self.assertEqual(["test_ignores_case_and_spaces"], failed, results)
        self.assertEqual(1, receipt["exit_code"], "the Python test fails on today's code")
        commands = [run["command"] for run in receipt["suites"]]
        self.assertEqual(2, len(commands), commands)
        self.assertTrue(commands[0].startswith("node ") and commands[0].endswith("--test frontend/routing.test.mjs"))
        self.assertTrue(commands[1].startswith("cd backend && "), commands)
        self.assertIn("tests/test_grader.py", commands[1])

    def test_a_file_no_suite_there_runs_is_refused_by_name(self):
        # Without backend/'s manifest no project around the Python test has a suite, and the root's cannot run it.
        project = self.project({path: text for path, text in SPLIT.items() if path != "backend/pyproject.toml"})
        root = self.detect(project.root)
        self.assertEqual(
            "no test command runs these files: backend/tests/test_grader.py (the detected node suite "
            "`npm test --silent` cannot run them)",
            scratch_suites.suites(project.root, root, SPLIT_TESTS, self.detect),
        )
        with tempfile.TemporaryDirectory() as run_dir:
            receipt = verify.scratch_run(project.root, Path(run_dir), tests=SPLIT_TESTS)
        self.assertEqual((None, scratch_suites.suites(project.root, root, SPLIT_TESTS, self.detect)), (
            receipt["results"], receipt["error"]))  # fmt: skip

    def test_one_suite_that_runs_every_file_is_kept(self):
        project = self.project()
        root = self.detect(project.root)
        found = scratch_suites.suites(project.root, root, ["frontend/routing.test.mjs"], self.detect)
        self.assertEqual([(root, "node --test frontend/routing.test.mjs", "scratch-tests")], found)
        (backend,) = scratch_suites.suites(project.root, root, ["backend/tests/test_grader.py"], self.detect)
        self.assertEqual(("backend", "scratch-tests"), (backend[0].directory, backend[2]))


class MergedReceiptTests(unittest.TestCase):
    def test_results_join_and_the_failed_run_speaks_for_the_receipt(self):
        node = {"command": "node --test a.test.mjs", "exit_code": 0, "timed_out": False, "output": "node.log",
                "results": {"passed": ["a.test.mjs::t"], "failed": [], "skipped": [], "collection_errors": [],
                            "uncollected": [], "total": 1, "complete": True}, "results_expected": True}  # fmt: skip
        python = {**node, "command": "cd backend && python -m unittest", "exit_code": 1, "output": "python.log",
                  "results": {**node["results"], "passed": [], "failed": ["test_x.T.test_y"]}}  # fmt: skip
        receipt = scratch_suites.merged([node, python])
        self.assertEqual((1, "python.log", False), (receipt["exit_code"], receipt["output"], receipt["timed_out"]))
        self.assertEqual("node --test a.test.mjs ; cd backend && python -m unittest", receipt["command"])
        self.assertEqual(["a.test.mjs::t"], receipt["results"]["passed"])
        self.assertEqual(["test_x.T.test_y"], receipt["results"]["failed"])
        self.assertEqual((2, True), (receipt["results"]["total"], receipt["results"]["complete"]))
        self.assertEqual([node, python], receipt["suites"])
        self.assertIs(node, scratch_suites.merged([node]))
        # A run without per-test results leaves the receipt without them; one that did not finish is not complete.
        self.assertIsNone(scratch_suites.merged([node, {**python, "results": None}])["results"])
        unfinished = {**python, "results": {**python["results"], "complete": False}, "timed_out": True}
        merged = scratch_suites.merged([node, unfinished])
        self.assertEqual((False, True), (merged["results"]["complete"], merged["timed_out"]))


class PackageRequired(unittest.TestCase):
    def setUp(self):
        self.project = Project(MONOREPO)
        self.addCleanup(self.project.close)

    def test_an_in_place_run_needs_a_real_directory_with_files_git_sees(self):
        package_scope.require(self.project.root, GREET, committed=False)
        (self.project.root / "packages/empty").mkdir()
        (self.project.root / "linked").symlink_to(self.project.root / GREET, target_is_directory=True)
        for package, reason in (
            ("packages/none", "is not a directory"),
            ("packages/empty", "holds no files Git does not ignore"),
            ("linked", "is not a directory"),
            ("README.md", "is not a directory"),
        ):
            with self.subTest(package=package), self.assertRaisesRegex(ValueError, reason):
                package_scope.require(self.project.root, package, committed=False)

    def test_a_task_worktree_needs_the_package_committed(self):
        package_scope.require(self.project.root, GREET, committed=True)
        (self.project.root / "packages/new").mkdir()
        (self.project.root / "packages/new/module.py").write_text("VALUE = 1\n")
        package_scope.require(self.project.root, "packages/new", committed=False)
        with self.assertRaisesRegex(ValueError, "holds no committed files"):
            package_scope.require(self.project.root, "packages/new", committed=True)


if __name__ == "__main__":
    unittest.main()
