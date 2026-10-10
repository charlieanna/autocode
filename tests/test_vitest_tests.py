"""Fail-closed command and evidence boundaries for native Vitest named proof."""

import copy
import json
import shlex
import tempfile
import unittest
from pathlib import Path

import autocode_verify as verify
import autocode_vitest_tests as vitest


class CommandTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / "frontend").mkdir()
        (self.root / "frontend/package.json").write_text(json.dumps({"scripts": {"test": "vitest run"}}))

    def test_direct_and_local_package_scripts_keep_the_filter_and_add_owned_reporting(self):
        for command in (
            "vitest run src/example.test.js -t test_c1",
            "node node_modules/vitest/vitest.mjs run",
            "npx --no-install vitest run",
            "npm --prefix frontend test -- -t test_c1",
            "npm --prefix=frontend run test --silent",
            "npm --prefix frontend test\t--\t-t test_c1",
        ):
            with self.subTest(command=command):
                self.assertTrue(verify.expects_results(None, command, self.root))
                words = shlex.split(vitest.instrument(command, self.root / "proof.json", self.root))
                self.assertEqual(shlex.split(command), words[: len(shlex.split(command))])
                self.assertIn("--exclude=**/.autocode/**", words)
                self.assertIn("--outputFile=" + str((self.root / "proof.json").resolve()), words)
                self.assertTrue(any(word.endswith("/autocode_vitest_reporter.cjs") for word in words))

    def test_no_implicit_install_watch_custom_reporter_shell_or_argument_confusion(self):
        for command in (
            "vitest",
            "vitest list run",
            "vitest --mode run",
            "vitest run --watch",
            "vitest run --run=false",
            "vitest run --reporter=json",
            "vitest run --output-file=x",
            "vitest run -- --reporter=json",
            "npx vitest run",
            "vitest run | cat",
            "vitest run; true",
            "vitest run\ntrue",
            "vitest run $(echo a)",
            'vitest run "unterminated',
            "npm --prefix .. test",
            "npm --prefix frontend test -- -w",
            "npm --prefix frontend --script-shell bash test",
            "npm install vitest",
        ):
            with self.subTest(command=command):
                self.assertIsNone(vitest.command_words(command, self.root))
                self.assertEqual(command, vitest.instrument(command, self.root / "proof.json", self.root))

    def test_package_prefix_symlink_cycle_is_not_a_supported_command(self):
        (self.root / "loop").symlink_to("loop", target_is_directory=True)
        self.assertIsNone(vitest.command_words("npm --prefix loop test", self.root))

    def test_a_package_wrapper_or_shell_script_is_not_a_native_vitest_command(self):
        for script in ("node wrapper.cjs", "vitest run && echo done", "vitest run --reporter=json", None):
            (self.root / "frontend/package.json").write_text(json.dumps({"scripts": {"test": script}}))
            self.assertIsNone(vitest.command_words("npm --prefix frontend test", self.root))
        self.assertIsNone(vitest.command_words("npm test"))


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.path = self.root / "proof.vitest.json"
        self.report = {
            "protocol": "autocode-vitest-tests",
            "version": 1,
            "vitest_version": "4.1.6",
            "complete": True,
            "reason": "passed",
            "unhandled_errors": 0,
            "total": 1,
            "files": [
                {
                    "file": str(self.root / "a.test.js"),
                    "collection_error": False,
                    "tests": [{"name": "group > test_c1_case", "state": "passed", "collection_error": False}],
                }
            ],
        }

    def read(self, report):
        self.path.write_text(json.dumps(report))
        return vitest.results(self.path, self.root)

    def test_only_executed_passes_are_credited(self):
        self.assertEqual(["a.test.js::group > test_c1_case"], self.read(self.report)["passed"])
        self.report["files"][0]["tests"][0]["state"] = "skipped"
        result = self.read(self.report)
        self.assertEqual([], result["passed"])
        self.assertEqual(["a.test.js::group > test_c1_case"], result["skipped"])
        self.report["total"] = 0
        self.report["files"][0]["tests"] = []
        self.assertEqual([], self.read(self.report)["passed"])

    def test_failed_assertion_and_failed_fixture_have_different_proof_meanings(self):
        self.report["reason"] = "failed"
        test = self.report["files"][0]["tests"][0]
        test["state"] = "failed"
        result = self.read(self.report)
        self.assertEqual(["a.test.js::group > test_c1_case"], result["failed"])
        self.assertEqual([], result["collection_errors"])
        self.assertEqual([], result["uncollected"])
        test["collection_error"] = True
        result = self.read(self.report)
        self.assertEqual(result["failed"], result["collection_errors"])
        self.assertEqual([], result["passed"])
        # A failed fixture executed its test; only a file that never collected is uncollected (#503).
        self.assertEqual([], result["uncollected"])
        self.report["files"][0]["collection_error"] = True
        result = self.read(self.report)
        self.assertEqual(["a.test.js::[collection]"], result["uncollected"])
        self.assertIn("a.test.js::[collection]", result["failed"])

    def test_duplicate_names_files_bad_counts_and_incomplete_results_are_rejected(self):
        variants = []
        for key, value in (
            ("total", 2),
            ("total", True),
            ("complete", False),
            ("complete", 1),
            ("version", True),
            ("vitest_version", "3.2.0"),
            ("reason", "interrupted"),
            ("unhandled_errors", True),
            ("files", None),
        ):
            report = copy.deepcopy(self.report)
            report[key] = value
            variants.append(report)
        report = copy.deepcopy(self.report)
        report["files"] *= 2
        report["total"] = 2
        variants.append(report)
        report = copy.deepcopy(self.report)
        report["files"][0]["tests"] *= 2
        report["total"] = 2
        variants.append(report)
        for state in ("pending", "todo", None):
            report = copy.deepcopy(self.report)
            report["files"][0]["tests"][0]["state"] = state
            variants.append(report)
        for report in variants:
            with self.subTest(report=report):
                self.assertIsNone(self.read(report))

    def test_stdout_ordinary_json_and_missing_sidecar_cannot_supply_proof(self):
        for value in ("PASS test_c1_case", {"success": True, "numPassedTests": 1}, None):
            self.assertIsNone(self.read(value))
        self.path.write_text('{"protocol":')
        self.assertIsNone(vitest.results(self.path, self.root))
        self.path.unlink()
        self.assertIsNone(vitest.results(self.path, self.root))

    def test_outside_paths_and_unhandled_errors_cannot_be_credited(self):
        for file in ("../outside.test.js", str(self.root.parent / "outside.test.js")):
            report = copy.deepcopy(self.report)
            report["files"][0]["file"] = file
            self.assertIsNone(self.read(report))
        self.report["unhandled_errors"] = 1
        self.assertIsNone(self.read(self.report))
        self.report["reason"] = "failed"
        self.assertIn("[vitest runtime error]", self.read(self.report)["collection_errors"])
        self.assertEqual([], self.read(self.report)["uncollected"])
