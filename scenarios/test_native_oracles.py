"""Counterexamples for native oracle evidence, with no compiler or npm downloads."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import native_tests, npm_dependencies, oracle


def receipt(stdout="", exit_code=0):
    return subprocess.CompletedProcess(["native-runner"], exit_code, stdout, "")


def go_events(*cases):
    events = [{"Action": "start", "Package": "example.test/project"}]
    for name, outcome in cases:
        events.extend(
            [
                {"Action": "run", "Package": "example.test/project", "Test": name},
                {"Action": outcome, "Package": "example.test/project", "Test": name},
            ]
        )
    events.append({"Action": "pass", "Package": "example.test/project"})
    return "\n".join(json.dumps(event) for event in events)


def vitest_report(*cases):
    passed = sum(status == "passed" for _, status in cases)
    failed = sum(status == "failed" for _, status in cases)
    skipped = len(cases) - passed - failed
    return {
        "success": not failed,
        "numTotalTestSuites": 1,
        "numPassedTestSuites": int(not failed),
        "numFailedTestSuites": int(bool(failed)),
        "numPendingTestSuites": 0,
        "numTotalTests": len(cases),
        "numPassedTests": passed,
        "numFailedTests": failed,
        "numPendingTests": skipped,
        "numTodoTests": 0,
        "testResults": [
            {
                "name": "/project/tests/cases.test.ts",
                "status": "failed" if failed else "passed",
                "assertionResults": [
                    {"fullName": name, "title": name, "ancestorTitles": [], "status": status} for name, status in cases
                ],
            }
        ],
    }


class NativeReporterCounterexamples(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="native-oracle-report-")
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name)
        (self.project / "tests").mkdir()
        (self.project / "tests/cases.test.js").write_text("// Native runner fixture.\n")

    def execute(self, runner, stdout="", exit_code=0, report=None):
        def run(command, cwd, **kwargs):
            if report is not None:
                (cwd / ".oracle-vitest.json").write_text(report if isinstance(report, str) else json.dumps(report))
            return receipt(stdout, exit_code)

        return native_tests.execute(self.project, runner, run)

    def test_go_empty_success_and_no_packages_cannot_be_a_pass(self):
        for stdout, code in (("", 0), ('go: warning: "./..." matched no packages\n', 1)):
            with self.subTest(stdout=stdout, code=code):
                self.assertFalse(self.execute("go", stdout, code).passed)

    def test_go_all_skipped_cannot_be_a_pass(self):
        self.assertFalse(self.execute("go", go_events(("TestC1", "skip"), ("TestC2", "skip"))).passed)

    def test_go_completed_real_cases_can_pass(self):
        self.assertTrue(self.execute("go", go_events(("TestC1", "pass"), ("TestC2", "pass"))).passed)

    def test_go_malformed_records_do_not_supply_evidence_or_crash(self):
        for stdout in ("true", "[]", '{"Action":"pass","Test":"TestC1"}'):
            with self.subTest(stdout=stdout):
                self.assertFalse(self.execute("go", stdout).passed)

    def test_go_unfinished_stream_cannot_be_a_pass(self):
        stdout = json.dumps({"Action": "pass", "Package": "example.test/project", "Test": "TestC1"})
        self.assertFalse(self.execute("go", stdout).passed)

    def test_node_all_skipped_suite_cannot_be_a_named_pass(self):
        tap = """TAP version 13
# Subtest: test_c1_parent
    # Subtest: skipped leaf
    ok 1 - skipped leaf # SKIP
      ---
      duration_ms: 0.1
      type: 'test'
      ...
    1..1
ok 1 - test_c1_parent
  ---
  duration_ms: 0.2
  type: 'suite'
  ...
1..1
# tests 1
# suites 1
# pass 0
# fail 0
# cancelled 0
# skipped 1
# todo 0
# duration_ms 1.0
"""
        self.assertFalse(self.execute("node", tap).passed)

    def test_node_unfinished_tap_cannot_be_a_pass(self):
        self.assertFalse(self.execute("node", "TAP version 13\nok 1 - test_c1\n").passed)

    def test_node_completed_cases_can_pass_beside_an_unclaimed_skipped_case(self):
        tap = """TAP version 13
# Subtest: test_c1
ok 1 - test_c1
# Subtest: test_c2
ok 2 - test_c2
# Subtest: optional case
ok 3 - optional case # SKIP
1..3
# tests 3
# suites 0
# pass 2
# fail 0
# cancelled 0
# skipped 1
# todo 0
# duration_ms 1.0
"""
        result = self.execute("node", tap)
        self.assertTrue(result.passed)
        self.assertNotIn("optional case", result.names)

    def test_vitest_missing_report_cannot_reuse_a_previous_pass(self):
        stale = self.project / ".oracle-vitest.json"
        stale.write_text(json.dumps(vitest_report(("test_c1", "passed"))))
        self.assertFalse(self.execute("vitest").passed)
        self.assertFalse(stale.exists())

    def test_vitest_malformed_report_is_rejected_and_cleaned(self):
        for report in (
            "{invalid",
            "[]",
            "true",
            {"testResults": [None]},
            {"testResults": [{"assertionResults": [None]}]},
            {
                **vitest_report(("test_c1", "passed")),
                "testResults": [{"assertionResults": [{"status": [], "fullName": "test_c1"}]}],
            },
        ):
            with self.subTest(report=report):
                self.assertFalse(self.execute("vitest", report=report).passed)
                self.assertFalse((self.project / ".oracle-vitest.json").exists())

    def test_vitest_incomplete_report_with_one_pass_is_rejected(self):
        report = {"testResults": [{"assertionResults": [{"status": "passed", "fullName": "test_c1"}]}]}
        self.assertFalse(self.execute("vitest", report=report).passed)

    def test_vitest_all_skipped_cannot_be_a_pass(self):
        self.assertFalse(
            self.execute("vitest", report=vitest_report(("test_c1", "pending"), ("test_c2", "pending"))).passed
        )

    def test_vitest_completed_real_cases_pass_and_report_is_cleaned(self):
        result = self.execute("vitest", report=vitest_report(("test_c1", "passed"), ("test_c2", "passed")))
        self.assertTrue(result.passed)
        self.assertFalse((self.project / ".oracle-vitest.json").exists())

    def test_vitest_counts_must_match_the_cases_it_claims(self):
        report = vitest_report(("test_c1", "passed"))
        report["numTotalTests"] = 2
        self.assertFalse(self.execute("vitest", report=report).passed)


class NativeDifferentialCounterexamples(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="native-oracle-diff-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.seed = self.root / "seed"
        self.seed.mkdir()
        self.project = self.root / "project"
        self.project.mkdir()
        (self.root / "hidden").mkdir()
        self.scenario = SimpleNamespace(
            seed=self.seed, dir=self.root, fake_criteria={"C1": "test: TestC1", "C2": "test: TestC2"}
        )
        for root in (self.seed, self.project):
            (root / "app").mkdir()
            (root / "app/source.go").write_text("package app\n")
            (root / "go.mod").write_text("module example.test/project\ngo 1.22\n")

    def suite(self, names, failed=()):
        return native_tests.Suite(receipt(exit_code=int(bool(failed))), set(names), set(failed))

    def test_one_regression_failure_cannot_cover_two_required_cases(self):
        rows = [
            self.suite({"guard", "TestC1", "TestC2"}),
            self.suite({"TestOracle"}),
            self.suite({"guard"}),
            self.suite({"guard", "TestC1", "TestC2"}, {"TestC1"}),
        ]
        with patch.object(native_tests, "execute", side_effect=rows):
            checks = oracle.go_change_checks(self.project, self.scenario, "app")
        self.assertFalse(next(check.ok for check in checks if check.name == "new_tests_fail_on_original_code"))

    def test_failure_that_already_existed_is_not_a_new_regression(self):
        rows = [
            self.suite({"guard", "TestC1", "TestC2"}),
            self.suite({"TestOracle"}),
            self.suite({"guard", "TestC1"}, {"TestC1"}),
            self.suite({"guard", "TestC1", "TestC2"}, {"TestC1", "TestC2"}),
        ]
        with patch.object(native_tests, "execute", side_effect=rows):
            checks = oracle.go_change_checks(self.project, self.scenario, "app")
        self.assertFalse(next(check.ok for check in checks if check.name == "new_tests_fail_on_original_code"))

    def test_missing_existing_guard_is_rejected(self):
        rows = [
            self.suite({"TestC1", "TestC2"}),
            self.suite({"TestOracle"}),
            self.suite({"guard"}),
            self.suite({"TestC1", "TestC2"}, {"TestC1", "TestC2"}),
        ]
        with patch.object(native_tests, "execute", side_effect=rows):
            checks = oracle.go_change_checks(self.project, self.scenario, "app")
        self.assertFalse(next(check.ok for check in checks if check.name == "existing_tests_kept"))

    def test_original_source_replay_keeps_delivered_go_test_files(self):
        regression = "package app\n// Delivered TestC1 and TestC2 regressions.\n"
        (self.project / "app/regression_test.go").write_text(regression)
        original = "package app\n// original behavior\n"
        (self.seed / "app/source.go").write_text(original)
        (self.project / "app/source.go").write_text("package app\n// fixed behavior\n")

        def execute(project, runner, run, *, hidden=False):
            if hidden:
                return self.suite({"TestOracle"})
            source = (project / "app/source.go").read_text()
            tests = project / "app/regression_test.go"
            if not tests.exists():
                return self.suite({"guard"})
            self.assertEqual(regression, tests.read_text())
            return self.suite({"guard", "TestC1", "TestC2"}, {"TestC1", "TestC2"} if source == original else set())

        before = {
            str(path.relative_to(self.project)): path.read_bytes() for path in self.project.rglob("*") if path.is_file()
        }
        with patch.object(native_tests, "execute", side_effect=execute):
            checks = oracle.go_change_checks(self.project, self.scenario, "app")
        self.assertTrue(next(check.ok for check in checks if check.name == "new_tests_fail_on_original_code"))
        self.assertEqual(
            before,
            {
                str(path.relative_to(self.project)): path.read_bytes()
                for path in self.project.rglob("*")
                if path.is_file()
            },
        )

    def test_first_go_setup_failure_cannot_count_as_missing_old_project(self):
        (self.seed / "app/source.go").unlink()
        (self.seed / "go.mod").unlink()
        for code, output in (
            (127, "go: command not found"),
            (-1, "TIMEOUT after 120s"),
            (1, "unrelated compiler setup failure"),
        ):
            with self.subTest(code=code, output=output):
                absent = native_tests.Suite(receipt(output, code), set(), set(), complete=False)
                rows = [self.suite({"TestC1", "TestC2"}), self.suite({"TestOracle"}), absent, absent]
                with patch.object(native_tests, "execute", side_effect=rows):
                    checks = oracle.go_change_checks(self.project, self.scenario, "app")
                self.assertFalse(next(check.ok for check in checks if check.name == "new_tests_fail_on_original_code"))

    def test_first_go_known_no_packages_does_not_require_an_old_suite(self):
        (self.seed / "app/source.go").unlink()
        (self.seed / "go.mod").unlink()
        absent = native_tests.Suite(
            receipt('go: warning: "./..." matched no packages\nno packages to test', 1), set(), set(), complete=False
        )
        rows = [self.suite({"TestC1", "TestC2"}), self.suite({"TestOracle"}), absent, absent]
        with patch.object(native_tests, "execute", side_effect=rows):
            checks = oracle.go_change_checks(self.project, self.scenario, "app")
        self.assertTrue(next(check.ok for check in checks if check.name == "new_tests_fail_on_original_code"))


class NpmSetupBoundaryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="native-oracle-npm-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.seed = self.root / "seed"
        self.seed.mkdir()
        (self.seed / "package.json").write_text('{"private":true}')
        (self.seed / "package-lock.json").write_text('{"lockfileVersion":3}')
        self.project = self.root / "project"
        self.project.mkdir()
        owner = patch.object(npm_dependencies, "__file__", str(self.root / "scenarios/harness/npm_dependencies.py"))
        owner.start()
        self.addCleanup(owner.stop)

    def test_production_environment_cannot_omit_the_runner_or_platform_binaries(self):
        def install(command, cwd, **kwargs):
            self.assertEqual("production", os.environ["NODE_ENV"])
            self.assertEqual("dev optional", os.environ["NPM_CONFIG_OMIT"])
            self.assertIn("--include=dev", command)
            self.assertIn("--include=optional", command)
            self.assertIn("--ignore-scripts", command)
            modules = cwd / "node_modules"
            (modules / "vitest").mkdir(parents=True)
            (modules / "vitest/vitest.mjs").write_text("pinned runner")
            return receipt()

        with (
            patch.dict(os.environ, {"NODE_ENV": "production", "npm_config_omit": "dev optional"}),
            patch.object(npm_dependencies.subprocess, "run", side_effect=install) as run,
        ):
            npm_dependencies.prepare(self.seed, self.project)
        run.assert_called_once()
        self.assertEqual("pinned runner", (self.project / "node_modules/vitest/vitest.mjs").read_text())

    def test_manifest_only_ready_cache_cannot_satisfy_the_current_install_policy(self):
        legacy_digest = hashlib.sha256(
            b"\0".join((self.seed / name).read_bytes() for name in ("package.json", "package-lock.json"))
        ).hexdigest()
        legacy = self.root / ".scenario-runs/npm-dependencies" / legacy_digest
        (legacy / "node_modules").mkdir(parents=True)
        (legacy / "ready").write_text(legacy_digest + "\n")
        (legacy / "node_modules/omitted-dev-marker").write_text("old incomplete cache")

        def install(command, cwd, **kwargs):
            self.assertNotEqual(legacy, cwd)
            (cwd / "node_modules/vitest").mkdir(parents=True)
            (cwd / "node_modules/vitest/vitest.mjs").write_text("pinned runner")
            return receipt()

        with patch.object(npm_dependencies.subprocess, "run", side_effect=install) as run:
            npm_dependencies.prepare(self.seed, self.project)
            second = self.root / "second-project"
            second.mkdir()
            npm_dependencies.prepare(self.seed, second)
        run.assert_called_once()
        self.assertEqual("pinned runner", (second / "node_modules/vitest/vitest.mjs").read_text())
        self.assertFalse((self.project / "node_modules/omitted-dev-marker").exists())
        self.assertEqual("old incomplete cache", (legacy / "node_modules/omitted-dev-marker").read_text())

    def test_ready_marker_from_another_install_policy_forces_reinstallation(self):
        def install(command, cwd, **kwargs):
            (cwd / "node_modules").mkdir(exist_ok=True)
            return receipt()

        with patch.object(npm_dependencies.subprocess, "run", side_effect=install) as run:
            npm_dependencies.prepare(self.seed, self.project)
            cache = next((self.root / ".scenario-runs/npm-dependencies").iterdir())
            (cache / "ready").write_text("earlier-install-policy\n")
            second = self.root / "second-project"
            second.mkdir()
            npm_dependencies.prepare(self.seed, second)
        self.assertEqual(2, run.call_count)
        self.assertEqual(cache.name + "\n", (cache / "ready").read_text())

    def test_missing_or_linked_manifest_is_refused_before_npm(self):
        manifest = self.seed / "package-lock.json"
        manifest.unlink()
        with patch.object(npm_dependencies.subprocess, "run") as run:
            with self.assertRaises(ValueError):
                npm_dependencies.prepare(self.seed, self.project)
            outside = self.root / "outside-lock.json"
            outside.write_text('{"lockfileVersion":3}')
            manifest.symlink_to(outside)
            with self.assertRaises(ValueError):
                npm_dependencies.prepare(self.seed, self.project)
            run.assert_not_called()
            self.assertEqual('{"lockfileVersion":3}', outside.read_text())

    def test_cached_dependency_symlink_cannot_redirect_a_fresh_project(self):
        def install(command, cwd, **kwargs):
            (cwd / "node_modules").mkdir()
            return receipt()

        with patch.object(npm_dependencies.subprocess, "run", side_effect=install) as run:
            npm_dependencies.prepare(self.seed, self.project)
            cache = next((self.root / ".scenario-runs/npm-dependencies").iterdir())
            modules = cache / "node_modules"
            shutil.rmtree(modules)
            outside = self.root / "foreign-dependencies"
            outside.mkdir()
            marker = outside / "marker"
            marker.write_text("foreign files")
            modules.symlink_to(outside, target_is_directory=True)
            second = self.root / "second-project"
            second.mkdir()
            with self.assertRaises(ValueError):
                npm_dependencies.prepare(self.seed, second)
            self.assertEqual(1, run.call_count)
            self.assertFalse((second / "node_modules").exists())
            self.assertEqual("foreign files", marker.read_text())

    def test_prepared_projects_cannot_modify_each_other_or_the_cached_runtime(self):
        def install(command, cwd, **kwargs):
            modules = cwd / "node_modules"
            (modules / "vitest").mkdir(parents=True)
            (modules / "vitest/vitest.mjs").write_text("pinned runtime")
            (modules / ".bin").mkdir()
            (modules / ".bin/vitest").symlink_to("../vitest/vitest.mjs")
            return receipt()

        with patch.object(npm_dependencies.subprocess, "run", side_effect=install) as run:
            npm_dependencies.prepare(self.seed, self.project)
            second = self.root / "second-project"
            second.mkdir()
            npm_dependencies.prepare(self.seed, second)
            first_runtime = self.project / "node_modules/vitest/vitest.mjs"
            first_runtime.write_text("changed by first project")
            self.assertEqual("pinned runtime", (second / "node_modules/vitest/vitest.mjs").read_text())
            cache = next((self.root / ".scenario-runs/npm-dependencies").iterdir())
            self.assertEqual("pinned runtime", (cache / "node_modules/vitest/vitest.mjs").read_text())
            self.assertFalse((self.project / "node_modules").is_symlink())
            self.assertTrue((second / "node_modules/.bin/vitest").resolve().is_relative_to(second.resolve()))
            self.assertEqual(1, run.call_count)


if __name__ == "__main__":
    unittest.main()
