"""Per-test results from RSpec's built-in JSON formatter (#2889).

The reports below are RSpec 3.13.7's own output for a small spec file, trimmed of run times and
exception detail. tests.test_package_scope.PolyglotSuite runs a real RSpec suite through the proof.
"""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

import autocode_rspec_tests as rspec_tests
import autocode_verify as verify
from autocode_test_framework import Framework


def example(n, name, status, *, file="./spec/grader_spec.rb"):
    return {"id": f"{file}[1:{n}]", "description": name, "full_description": f"Grader {name}", "status": status,
            "file_path": file, "line_number": n + 2, "run_time": 0.001, "pending_message": None}  # fmt: skip


REPORT = {
    "version": "3.13.7",
    "examples": [
        example(1, "test_exact_match_passes", "passed"),
        example(2, "test_ignores_case", "failed"),
        example(4, "test_pending", "pending"),
        {**example(3, "test_param 1", "passed"), "full_description": "Grader with params test_param 1"},
    ],
    "summary": {
        "duration": 0.01,
        "example_count": 4,
        "failure_count": 1,
        "pending_count": 1,
        "errors_outside_of_examples_count": 0,
    },
    "summary_line": "4 examples, 1 failure, 1 pending",
    "messages": [],
}
# A spec file that requires code the fix adds fails to load, and RSpec then runs no example at all.
LOAD_ERROR = {
    "version": "3.13.7",
    "messages": [
        "\nAn error occurred while loading ./spec/new_spec.rb. - Did you mean?\n                    rspec "
        "./spec/grader_spec.rb\n\nFailure/Error: require_relative '../app/missing'\n\nLoadError:\n  cannot load "
        "such file -- /project/app/missing\n# ./spec/new_spec.rb:1:in `require_relative'\n"
    ],
    "examples": [],
    "summary": {
        "duration": 0.01,
        "example_count": 0,
        "failure_count": 0,
        "pending_count": 0,
        "errors_outside_of_examples_count": 1,
    },
    "summary_line": "0 examples, 0 failures, 1 error occurred outside of examples",
}


class ResultsTests(unittest.TestCase):
    def parsed(self, report):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "suite.rspec.json"
            path.write_text(json.dumps(report))
            return rspec_tests.results(path)

    def test_each_example_is_named_by_its_file_and_full_description(self):
        self.assertEqual(
            {
                "passed": [
                    "spec/grader_spec.rb::Grader test_exact_match_passes",
                    "spec/grader_spec.rb::Grader with params test_param 1",
                ],
                "failed": ["spec/grader_spec.rb::Grader test_ignores_case"],
                "skipped": ["spec/grader_spec.rb::Grader test_pending"],
                "collection_errors": [],
                "uncollected": [],
                "total": 4,
                "complete": True,
            },
            self.parsed(REPORT),
        )

    def test_a_spec_file_that_fails_to_load_is_a_failed_uncollected_entry(self):
        found = self.parsed(LOAD_ERROR)
        self.assertEqual(["spec/new_spec.rb::[collection]"], found["failed"])
        self.assertEqual(found["failed"], found["uncollected"])
        self.assertEqual(found["failed"], found["collection_errors"])
        self.assertFalse(verify.schedule.complete_results({"results": found}), "nothing in it ran")

    def test_ambiguous_or_inconsistent_evidence_gives_no_results(self):
        def changed(edit):
            report = copy.deepcopy(REPORT)
            edit(report)
            return report

        cases = {
            "an error outside every example that names no file": changed(
                lambda r: r["summary"].update(errors_outside_of_examples_count=1)
            ),
            "two examples with one id": changed(lambda r: r["examples"].append(dict(r["examples"][0]))),
            "a count the summary disagrees with": changed(lambda r: r["summary"].update(failure_count=0)),
            "an unknown status": changed(lambda r: r["examples"][0].update(status="errored")),
            "a file outside the project": changed(lambda r: r["examples"][0].update(file_path="../other_spec.rb")),
            "no example and no load error": {**LOAD_ERROR, "messages": []},
        }
        for name, report in cases.items():
            with self.subTest(name):
                self.assertIsNone(self.parsed(report))
        with tempfile.TemporaryDirectory() as folder:
            self.assertIsNone(rspec_tests.results(Path(folder) / "missing.rspec.json"))
            (Path(folder) / "bad.rspec.json").write_text("{")
            self.assertIsNone(rspec_tests.results(Path(folder) / "bad.rspec.json"))


class CommandTests(unittest.TestCase):
    def test_a_plain_rspec_command_writes_json_beside_its_progress_output(self):
        out = Path("/evidence/suite.rspec.json")
        self.assertEqual(
            "bundle exec rspec spec/a_spec.rb --format progress --format json --out /evidence/suite.rspec.json",
            rspec_tests.instrument("bundle exec rspec spec/a_spec.rb", out),
        )
        # A formatter the command names keeps stdout; RSpec's own formatter is only added to it.
        self.assertEqual(
            "rspec --format documentation --format json --out /evidence/suite.rspec.json",
            rspec_tests.instrument("rspec --format documentation", out),
        )

    def test_other_commands_are_left_alone(self):
        for command in (
            "bundle exec rspec --format RspecJunitFormatter --out junit.xml",
            "rspec -o report.txt",
            "rspec --options custom.rspec",
            "cd backend && bundle exec rspec",
            "bundle exec rake spec",
            "npm test",
            "rspec 'unterminated",
            "",
            None,
        ):
            with self.subTest(command=command):
                self.assertIsNone(rspec_tests.command_words(command))
                self.assertEqual(command, rspec_tests.instrument(command, "/evidence/x.rspec.json"))

    def test_the_proof_expects_and_reads_rspec_results(self):
        rspec = Framework("rspec", "bundle exec rspec", runner="bundle exec rspec")
        self.assertTrue(rspec.per_test)
        self.assertTrue(verify.expects_results(rspec, "bundle exec rspec spec/a_spec.rb"))
        self.assertFalse(verify.expects_results(rspec, "bundle exec rake spec"))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "suite-on-base.rspec.json"
            path.write_text(json.dumps(REPORT))
            self.assertEqual(4, verify.per_test_results(rspec, {}, path)["total"])


if __name__ == "__main__":
    unittest.main()
