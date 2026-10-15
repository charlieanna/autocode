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

import autocode_native_test_names as native_names
import autocode_package_scope as package_scope
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
        proof = [{"contract": {"kind": "proof"}}]
        packaged = {"test_command": "cd packages/svc && go test ./...", "python": sys.executable}
        task_preflight._proof_commands({"settings": {**settings, "regression": packaged}}, project.root, proof)
        for refused in (
            {**root_settings, "regression": packaged},
            {**settings, "regression": {**packaged, "test_command": "cd packages/svc && go vet ./..."}},
        ):
            with self.subTest(settings=refused), self.assertRaisesRegex(ValueError, "cannot supply the named results"):
                task_preflight._proof_commands({"settings": refused}, project.root, proof)

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
