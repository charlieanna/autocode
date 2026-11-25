"""Tests that share one name in one file are one identity, failed when any of them failed.

Real suites repeat names (setup sweep, docs/setup-sweep.md): httparty's RSpec suite has two one-liners
described alike in one file, ufo's Vitest suite two `test.each` rows alike. Each parser refused the
whole report, so a passing suite was "broken" on the base. Every parser now reads them through
autocode_repeated_tests: the same answer the Jest and Cargo readers already gave.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import autocode_jest_tests as jest_tests
import autocode_minitest_tests as minitest_tests
import autocode_mocha_tests as mocha_tests
import autocode_repeated_tests as repeated_tests
import autocode_rspec_tests as rspec_tests
import autocode_vitest_tests as vitest_tests


class Settle(unittest.TestCase):
    def test_a_failure_of_one_test_of_a_name_fails_the_name(self):
        self.assertEqual(
            {"passed": {"b"}, "failed": {"a"}, "skipped": {"c"}},
            repeated_tests.settle({"a": {"passed", "failed"}, "b": {"passed", "skipped"}, "c": {"skipped"}}),
        )


class Readers(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, self.root, True)
        self.report = self.root / "report.json"

    def write(self, value):
        self.report.write_text(json.dumps(value))
        return self.report

    def rspec(self, second):
        def example(n, name, status):
            return {"id": f"./spec/a_spec.rb[1:{n}]", "description": name, "full_description": f"A {name}",
                    "status": status, "file_path": "./spec/a_spec.rb", "line_number": n, "run_time": 0.0,
                    "pending_message": None}  # fmt: skip

        examples = [example(1, "is expected to be truthy", "passed"), example(2, "is expected to be truthy", second)]
        examples.append(example(3, "adds", "passed"))
        failed = sum(e["status"] == "failed" for e in examples)
        summary = {"duration": 0.1, "example_count": 3, "failure_count": failed, "pending_count": 0,
                   "errors_outside_of_examples_count": 0}  # fmt: skip
        return rspec_tests.results(self.write({"version": "3.13", "examples": examples, "summary": summary}))

    def test_rspec_one_liners_described_alike(self):
        found = self.rspec("passed")
        self.assertEqual(["spec/a_spec.rb::A adds", "spec/a_spec.rb::A is expected to be truthy"], found["passed"])
        self.assertEqual(2, found["total"])
        self.assertEqual(["spec/a_spec.rb::A is expected to be truthy"], self.rspec("failed")["failed"])

    def vitest(self, second):
        tests = [
            {"name": "withQuery > /?test with {}", "state": "passed", "collection_error": False},
            {"name": "withQuery > /?test with {}", "state": second, "collection_error": False},
        ]
        report = {"protocol": "autocode-vitest-tests", "version": 1, "vitest_version": "4.1.5", "complete": True,
                  "reason": "failed" if second == "failed" else "passed", "unhandled_errors": 0, "total": 2,
                  "files": [{"file": str(self.root / "q.test.ts"), "collection_error": False, "tests": tests}]}  # fmt: skip
        return vitest_tests.results(self.write(report), self.root)

    def test_vitest_rows_of_a_table_printed_alike(self):
        self.assertEqual(["q.test.ts::withQuery > /?test with {}"], self.vitest("passed")["passed"])
        failed = self.vitest("failed")
        self.assertEqual((["q.test.ts::withQuery > /?test with {}"], []), (failed["failed"], failed["passed"]))

    def mocha(self, second):
        file = str(self.root / "test/a.test.js")
        test = {"title": "works", "fullTitle": "x works", "file": file}
        passes = [test] + ([test] if second == "passed" else [])
        failures = [test] if second == "failed" else []
        report = {"stats": {"tests": 2, "passes": len(passes), "failures": len(failures), "pending": 0},
                  "tests": [test, test], "passes": passes, "failures": failures, "pending": []}  # fmt: skip
        return mocha_tests.results(self.write(report), self.root)

    def test_mocha_tests_titled_alike(self):
        self.assertEqual(["test/a.test.js::x > works"], self.mocha("passed")["passed"])
        failed = self.mocha("failed")
        self.assertEqual((["test/a.test.js::x > works"], []), (failed["failed"], failed["passed"]))

    def test_minitest_a_class_reopened_with_one_method_name(self):
        (self.root / "test").mkdir()
        (self.root / "test/a_test.rb").write_text("class ATest < Minitest::Test\n  def test_x; end\nend\n")
        out = "ATest#test_x = 0.00 s = .\nATest#test_x = 0.00 s = F\n2 runs, 2 assertions, 1 failures, 0 errors, 0 skips\n"
        found = minitest_tests.results(out, self.root)
        self.assertEqual((["test/a_test.rb::ATest::test_x"], []), (found["failed"], found["passed"]))

    def test_jest_reads_them_through_the_same_answer(self):
        test = {"title": "works", "ancestorTitles": ["x"], "status": "passed"}
        file = {"name": str(self.root / "a.test.js"), "status": "failed",
                "assertionResults": [test, {**test, "status": "failed"}]}  # fmt: skip
        report = {"numTotalTests": 2, "numFailedTests": 1, "numTotalTestSuites": 1, "success": False,
                  "testResults": [file]}  # fmt: skip
        found = jest_tests.results(self.write(report), self.root)
        self.assertEqual((["a.test.js::x > works"], []), (found["failed"], found["passed"]))


if __name__ == "__main__":
    unittest.main()
