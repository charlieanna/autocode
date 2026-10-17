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

import autocode_bug_job as bug_job
import autocode_native_test_names as native_names
import autocode_package_scope as package_scope
import autocode_preflight_worker as preflight_worker
import autocode_proof_stability as proof_stability
import autocode_protected_oracles as protected_oracles
import autocode_regression as regression
import autocode_run_view as run_view
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
        # The bug-fix Investigator's affected and test paths (autocode_bug_job.check, #711).
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
        paused = {"status": "PAUSED_VALIDATION", "next_stage": "sol"}
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

    def test_only_the_unchanged_diagnosis_note_the_runner_wrote_is_not_the_change(self):
        # The bug-fix workflow's note is at the repository root, under docs/bugs/ (autocode_bug_job): the proof,
        # like the check before review (tests.test_package_writes), leaves it out only while it is unchanged.
        from tests.test_bug_job import diagnosis

        note = "docs/bugs/blank-name.json"
        case = {"id": "blank", "given": "a blank name", "when": "greet runs", "then": "it is rejected"}
        report = diagnosis(
            note_path=note,
            test_cases=[case],
            affected_paths=[f"{GREET}/greet.py"],
            test_paths=[f"{GREET}/test_greet.py"],
        )
        output = self.run_dir / "bug-investigation-01.json"
        output.write_text(json.dumps(report))
        state = bugfix_state(self.project)
        bug_job.apply(state, report, {"changed_files": [], "output": str(output)}, self.project.root)
        self.project.write(FIX)
        proof = regression.prove(state, self.project.root, self.run_dir / "unchanged")
        self.assertEqual(verify.PASS, proof["verdict"], proof["failures"] + proof["unverified"])
        written = json.loads((self.project.root / note).read_text())
        self.project.write({note: json.dumps({**written, "conclusion": "edited"}, indent=2) + "\n"})
        proof = regression.prove(state, self.project.root, self.run_dir / "edited")
        self.assertIn(
            f"Files outside the package packages/greet/ changed, and its suite does not cover them: {note}",
            proof["unverified"],
        )


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
