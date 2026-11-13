"""Per-test results from RSpec's built-in JSON formatter (#2889).

The reports below are RSpec 3.13.7's own output for a small spec file, trimmed of run times and
exception detail. tests.test_package_scope.PolyglotSuite runs a real RSpec suite through the proof.
"""

from __future__ import annotations

import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import autocode_regression as regression
import autocode_rspec_tests as rspec_tests
import autocode_verify as verify
from autocode_test_framework import Framework

from tests.test_verify import Project


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


PAGER = (
    "module Pager\n  def self.page_count(total, size)\n"
    "    raise ArgumentError, 'size' unless size.positive?\n\n"
    "    (total + size - 1) / size\n  end\nend\n"
)
HELPER = PAGER.replace("    raise ArgumentError, 'size' unless size.positive?\n", "    need(size)\n").replace(
    "  end\nend\n",
    "  end\n\n  def self.need(size)\n    raise ArgumentError, 'size' unless size.positive?\n  end\nend\n",
)
PAGER_SPEC = (
    "require_relative '../lib/pager'\n\nRSpec.describe Pager do\n  describe '.page_count' do\n"
    "    it 'returns the page count for an exact multiple' do\n      expect(Pager.page_count(10, 5)).to eq(2)\n    end\n\n"
    '    it "counts a partial last page" do\n      expect(Pager.page_count(11, 5)).to eq(3)\n    end\n  end\nend\n'
)
EXACT = "spec/pager_spec.rb::Pager.page_count returns the page count for an exact multiple"
PARTIAL = "spec/pager_spec.rb::Pager.page_count counts a partial last page"


@unittest.skipUnless(shutil.which("bundle") and shutil.which("rspec"), "Bundler and RSpec run the project's suite")
class ExistingExampleTests(unittest.TestCase):
    """A plan guards a change with the examples an RSpec project already has, named by what each says.

    On real runs (2026-11-13) a bug fix's guards on existing examples were looked for as test_ac2_..., found
    nowhere, and the Builder copied both examples under those names after two failed proofs; a refactor was
    planned with no named test at all, could not be proved and asked the person to widen its scope.
    """

    def prove(self, files, guards, tests=(), *, pager=PAGER, spec=PAGER_SPEC, kind="build"):
        """The proof of ``files`` written over a project of ``pager`` and ``spec``, whose plan is ``tests`` and
        then ``guards``, each as the plan declares it."""
        project = Project(
            {
                "Gemfile": "source 'https://rubygems.org'\ngem 'rspec'\n",
                "lib/pager.rb": pager,
                "spec/pager_spec.rb": spec,
            }
        )
        self.addCleanup(project.close)
        self.assertEqual("bundle exec rspec", verify.detect_framework(project.root).suite)
        project.write(files)
        methods = [*("test: " + test for test in tests), *("guard: " + guard for guard in guards)]
        criteria = [
            {"id": f"AC{n}", "criterion": text, "verification_method": text} for n, text in enumerate(methods, 1)
        ]
        state = {
            "goal_contract": {"body": {"task_kind": kind, "acceptance_criteria": criteria}},
            "base_commit": project.base,
            "settings": {"regression": {"test_timeout": 60}},
        }
        return regression.prove(state, project.root, project.evidence / "proof")

    def test_a_refactor_that_changes_no_spec_is_proved_by_the_examples_it_guards(self):
        # By the example's own words, and by its full description.
        proof = self.prove(
            {"lib/pager.rb": HELPER},
            ["returns the page count for an exact multiple", '"Pager.page_count counts a partial last page"'],
        )
        self.assertEqual(verify.PASS, proof["verdict"], proof)
        self.assertEqual({"AC1": [EXACT], "AC2": [PARTIAL]}, proof["case_tests"])

    def test_a_refactor_that_breaks_a_guarded_example_is_not_proved(self):
        proof = self.prove(
            {"lib/pager.rb": HELPER.replace("(total + size - 1) / size", "total / size")},
            ["returns the page count for an exact multiple", "counts a partial last page"],
        )
        self.assertNotEqual(verify.PASS, proof["verdict"], proof)
        self.assertIn("AC2", " ".join(proof["failures"]), proof)

    def test_a_guard_that_no_example_says_is_not_proved(self):
        proof = self.prove({"lib/pager.rb": HELPER}, ["counts a partial", "partial last page counts"])
        self.assertNotEqual(verify.PASS, proof["verdict"], proof)
        self.assertEqual({"AC1": [], "AC2": []}, proof["case_tests"])

    def test_a_new_spec_file_does_not_take_the_existing_examples_out_of_the_suite(self):
        # Each example of spec/pager_spec.rb was "page" by name, the last word of its description, so a new spec
        # file that says "page" named them all and none could guard the change.
        added = (
            "require_relative '../lib/pager'\n\nRSpec.describe Pager do\n"
            "  it 'test_ac3_one_page for a short list' do\n    expect(Pager.page_count(1, 5)).to eq(1) # one page\n  end\nend\n"
        )
        proof = self.prove(
            {"lib/pager.rb": HELPER, "spec/short_spec.rb": added},
            ["returns the page count for an exact multiple", "counts a partial last page", "test_ac3_one_page"],
        )
        self.assertEqual(verify.PASS, proof["verdict"], proof)
        self.assertEqual(
            {"AC1": [EXACT], "AC2": [PARTIAL], "AC3": ["spec/short_spec.rb::Pager test_ac3_one_page for a short list"]},
            proof["case_tests"],
        )

    def test_a_fix_is_guarded_by_the_examples_its_spec_file_already_had(self):
        # The fix adds its example to the project's one spec file, whose other examples guard it.
        sizes = (
            "    it 'rejects a size that is not positive' do\n"
            "      expect { Pager.page_count(10, 0) }.to raise_error(ArgumentError)\n    end\n"
        )
        partial = '    it "counts a partial last page" do\n      expect(Pager.page_count(11, 5)).to eq(3)\n    end\n'
        spec = PAGER_SPEC.replace(partial, sizes)
        self.assertIn(sizes, spec)
        fixed = spec.replace(
            sizes, sizes + "\n" + partial.replace('"counts a partial last page"', "'test_ac1_partial'")
        )
        proof = self.prove(
            {"lib/pager.rb": PAGER, "spec/pager_spec.rb": fixed},
            ["returns the page count for an exact multiple", "rejects a size that is not positive"],
            ["test_ac1_partial"],
            pager=PAGER.replace("(total + size - 1) / size", "total / size"),
            spec=spec,
            kind="bug",
        )
        self.assertEqual(verify.PASS, proof["verdict"], proof)
        self.assertEqual(
            {
                "AC1": ["spec/pager_spec.rb::Pager.page_count test_ac1_partial"],
                "AC2": [EXACT],
                "AC3": ["spec/pager_spec.rb::Pager.page_count rejects a size that is not positive"],
            },
            proof["case_tests"],
        )


if __name__ == "__main__":
    unittest.main()
