"""A repair is shown by the project's own failing suite where no runner names a test.

"The test 'counts a partial last page' fails. Fix src/pager.js so that it passes. Do not change the tests", in
a Jest project (Claude models, 2026-11-13): the plan changed ``src/pager.js`` alone, the proof failed twice
with "No regression test was added or changed", and the Resolver asked the person to type a regression
command. The same request in a Python project completes: the runner names each test, so the test that fails on
the original code and passes with the change is the proof (first_suite.existing_flip). A suite that reports
only an exit code shows the same thing as a whole: it fails on the original code and passes with the change,
and the change is code alone, with the suite's own files as they were.

The cases run real commands in a Ruby project, with Minitest's detected suite and with a test script only the
plan names, and in a shell and a Makefile project; no provider runs.
tools/autocode_first_suite.py
"""

from __future__ import annotations

import shutil
import unittest
from unittest import mock

import autocode_first_suite as first_suite
import autocode_regression as regression
import autocode_util as util
import autocode_verify as verify
from autocode_test_framework import Framework

from .test_verify import Project

FIXED = "module Pager\n  def self.page_count(total, size)\n    (total + size - 1) / size\n  end\nend\n"
# Rounds down: eleven items in pages of five is three pages, and this says two.
BROKEN = FIXED.replace("(total + size - 1) / size", "total / size")
STILL_BROKEN = BROKEN.replace("total / size", "total.div(size)")
MINITEST = (
    'require "minitest/autorun"\nrequire_relative "../lib/pager"\n\nclass PagerTest < Minitest::Test\n'
    "  def test_exact_multiple\n    assert_equal 2, Pager.page_count(10, 5)\n  end\n\n"
    "  def test_partial_last_page\n    assert_equal 3, Pager.page_count(11, 5)\n  end\nend\n"
)
# A plain script under tests/: no runner AutoCode detects runs it, so the approved plan's command does.
SCRIPT = (
    'require_relative "../lib/pager"\n'
    'abort "exact multiple" unless Pager.page_count(10, 5) == 2\n'
    'abort "partial last page" unless Pager.page_count(11, 5) == 3\n'
)
NARROWED = SCRIPT.splitlines(keepends=True)[0] + SCRIPT.splitlines(keepends=True)[1]
RUN_SCRIPT = "`ruby tests/pager_check.rb` fails on the original code and passes with the fix"
SH_FIXED = "page_count() { echo $(( ($1 + $2 - 1) / $2 )); }\n"
SH_BROKEN = SH_FIXED.replace("($1 + $2 - 1) / $2", "$1 / $2")
SH_CHECK = '. ./pager.sh\n[ "$(page_count 11 5)" = 3 ] || { echo "partial last page"; exit 1; }\n'
NO_TEST = "No regression test was added or changed"
SHOWN = "fails on the original code (exit 1) and passes with the change, so the tests the project already has show"


def contract(*methods, kind="bugfix"):
    rows = [{"id": f"AC{n}", "criterion": "pages", "verification_method": m} for n, m in enumerate(methods, 1)]
    sealed = {"task_id": "task-1", "revision": 1, "body": {"task_kind": kind, "acceptance_criteria": rows}}
    return {**sealed, "hash": util.digest(sealed), "approval_status": "approved"}


class Proving(unittest.TestCase):
    seed: dict = {}

    def setUp(self):
        self.project = Project(self.seed)
        self.addCleanup(self.project.close)

    def prove(self, goal_contract, files):
        self.project.write(files)
        state = {
            "workspace": str(self.project.root),
            "base_commit": self.project.base,
            "iteration": 1,
            "stages": [],
            "history": [],
            "settings": {"regression": {}},
            "goal_contract": goal_contract,
        }
        with mock.patch.object(verify.diff_coverage, "collect", return_value=None):
            return regression.prove(state, self.project.root, self.project.root.parent / "run")

    def shown(self, proof):
        return any(SHOWN in note for note in proof["notes"])


class RuleTests(unittest.TestCase):
    def test_only_a_suite_whose_inputs_are_its_test_files_shows_a_repair_by_its_exit_code(self):
        suite = "npm test --silent"
        for framework, command, adopted, eligible in (
            (Framework("jest", suite), suite, None, True),
            (Framework("mocha", "npx --no-install mocha"), "npx --no-install mocha", None, True),
            (Framework("minitest", "ruby -e 1"), "ruby -e 1", None, True),
            (None, "ruby tests/pager_check.rb", "ruby tests/pager_check.rb", True),
            # A command the person typed, a recipe and a package script run whatever they say.
            (None, "sh run.sh", None, False),
            (Framework("jest", suite), "sh run.sh", None, False),
            (Framework("make", "make test"), "make test", None, False),
            (Framework("npm", suite), suite, None, False),
            (Framework("pytest", "python -m pytest"), "python -m pytest", None, False),
            (None, None, None, False),
        ):
            with self.subTest(framework=framework and framework.name, command=command):
                self.assertEqual(eligible, first_suite.exit_repair_suite(framework, command, adopted))

    def test_a_suite_fails_as_a_whole_when_it_completed_nonzero_without_naming_a_test(self):
        ran = lambda health, command="make test": {"command": command, "health": health}  # noqa: E731
        self.assertTrue(first_suite.repairable_exit(ran("failing"), "make test"))
        for base_suite in (ran("passing"), ran("failing_tests"), ran("broken"), ran("timeout"), None):
            self.assertFalse(first_suite.repairable_exit(base_suite, "make test"))
        self.assertFalse(first_suite.repairable_exit(ran("failing", "sh other.sh"), "make test"))
        self.assertFalse(first_suite.repairable_exit(ran("failing"), None))


@unittest.skipUnless(shutil.which("ruby"), "the project's tests are Ruby scripts")
class PlanCommandTests(Proving):
    """No runner is detected, so the approved plan's command is the suite (autocode_plan_proof_command)."""

    seed = {"lib/pager.rb": BROKEN, "tests/pager_check.rb": SCRIPT}

    def test_the_failing_suite_that_passes_with_the_change_shows_the_repair(self):
        self.assertIsNone(verify.detect_framework(self.project.root))
        proof = self.prove(contract(RUN_SCRIPT), {"lib/pager.rb": FIXED})
        self.assertEqual(verify.PASS, proof["verdict"], proof)
        self.assertEqual((["lib/pager.rb"], []), (proof["source_files"], proof["test_files"]))
        self.assertEqual("ruby tests/pager_check.rb", proof["commands"]["suite"])
        self.assertEqual(0, proof["checks"]["suite_on_candidate"]["exit_code"])
        self.assertTrue(self.shown(proof), proof["notes"])
        self.assertIn("its suite `ruby tests/pager_check.rb` fails", " ".join(proof["notes"]))
        self.assertIn(
            "the repair rests on the whole suite's exit code on the original code and with the change",
            proof["review_reasons"],
        )

    def test_a_change_that_leaves_the_suite_failing_shows_no_repair(self):
        proof = self.prove(contract(RUN_SCRIPT), {"lib/pager.rb": STILL_BROKEN})
        self.assertEqual(verify.FAIL, proof["verdict"], proof)
        self.assertIn(
            f"{NO_TEST}, and the suite that fails on the original code is not shown to pass with the change",
            " ".join(proof["failures"]),
        )
        self.assertFalse(self.shown(proof), proof["notes"])

    def test_a_change_to_the_tests_is_not_this_repair(self):
        # The candidate drops the failing check and leaves the product wrong: the ordinary proof judges it.
        proof = self.prove(contract(RUN_SCRIPT), {"lib/pager.rb": STILL_BROKEN, "tests/pager_check.rb": NARROWED})
        self.assertNotEqual(verify.PASS, proof["verdict"], proof)
        self.assertFalse(self.shown(proof), proof["notes"])

    def test_a_change_to_more_than_code_is_not_this_repair(self):
        # A file that is not code may be one the suite reads; the ordinary judgement stands.
        proof = self.prove(contract(RUN_SCRIPT), {"lib/pager.rb": FIXED, "Rakefile": "task :default\n"})
        self.assertEqual(verify.FAIL, proof["verdict"], proof)
        self.assertIn(f"{NO_TEST}, so the bug is not shown to be reproduced", " ".join(proof["failures"]))

    def test_a_suite_that_passes_on_the_original_code_shows_no_repair(self):
        working = Project({"lib/pager.rb": FIXED, "tests/pager_check.rb": SCRIPT})
        self.addCleanup(working.close)
        self.project = working
        proof = self.prove(contract(RUN_SCRIPT), {"lib/pager.rb": FIXED.replace("- 1)", "- 1 )")})
        self.assertEqual(verify.FAIL, proof["verdict"], proof)
        self.assertIn(f"{NO_TEST}, so the bug is not shown to be reproduced", " ".join(proof["failures"]))


@unittest.skipUnless(shutil.which("ruby"), "the project's suite is its Minitest files")
class MinitestTests(Proving):
    seed = {"lib/pager.rb": BROKEN, "test/pager_test.rb": MINITEST}

    def test_the_detected_suite_shows_the_repair(self):
        framework = verify.detect_framework(self.project.root)
        self.assertEqual("minitest", framework.name)
        criterion = "`ruby test/pager_test.rb` fails on the original code and passes with the fix"
        proof = self.prove(contract(criterion), {"lib/pager.rb": FIXED})
        self.assertEqual(verify.PASS, proof["verdict"], proof)
        self.assertEqual((framework.suite, "detected:minitest"), tuple(proof["commands"].values())[:2])
        self.assertTrue(self.shown(proof), proof["notes"])


class ShellTests(Proving):
    """A shell test sources the code under test, as a JavaScript test imports it (definition_scan.sourced_product)."""

    seed = {"pager.sh": SH_BROKEN, "tests/test_pager.sh": SH_CHECK}
    criterion = "`sh tests/test_pager.sh` fails on the original code and passes with the fix"

    def test_the_file_a_shell_test_sources_is_the_code_under_test(self):
        proof = self.prove(contract(self.criterion), {"pager.sh": SH_FIXED})
        self.assertEqual(verify.PASS, proof["verdict"], proof)
        self.assertEqual((["pager.sh"], []), (proof["source_files"], proof["test_files"]))
        self.assertTrue(self.shown(proof), proof["notes"])
        # The original test ran over the changed pager.sh: the suite's own files are the original ones.
        self.assertEqual(0, proof["checks"]["suite_base_definition_on_candidate"]["exit_code"])

    def test_a_script_the_test_runs_is_one_of_the_suites_own_files(self):
        # The test runs checks.sh, which holds the failing check. A change that drops the check from it and
        # leaves pager.sh wrong makes the suite pass by running less: the original checks.sh still fails.
        checks = '. ./pager.sh\n[ "$(page_count 10 5)" = 2 ] || exit 1\n[ "$(page_count 11 5)" = 3 ] || exit 1\n'
        ran = Project({"pager.sh": SH_BROKEN, "checks.sh": checks, "tests/test_pager.sh": "sh ./checks.sh\n"})
        self.addCleanup(ran.close)
        self.project = ran
        proof = self.prove(contract(self.criterion), {"checks.sh": "".join(checks.splitlines(keepends=True)[:2])})
        self.assertEqual(verify.FAIL, proof["verdict"], proof)
        self.assertIn(f"{NO_TEST}, and the suite that fails on the original code is not shown", proof["failures"][0])
        self.assertFalse(self.shown(proof), proof["notes"])


class OtherSuiteTests(Proving):
    """Suites whose inputs AutoCode cannot hold to the original: the missing-test judgement stands."""

    seed = {"pager.sh": SH_BROKEN, "tests/test_pager.sh": SH_CHECK}

    @unittest.skipUnless(shutil.which("make"), "the project's suite is make test")
    def test_a_recipe_can_run_anything_so_make_test_shows_no_repair(self):
        made = Project({**self.seed, "Makefile": "test:\n\tsh tests/test_pager.sh\n\n.PHONY: test\n"})
        self.addCleanup(made.close)
        self.project = made
        self.assertEqual("make test", verify.detect_framework(made.root).suite)
        proof = self.prove(
            contract("`make test` fails on the original code and passes with the fix"), {"pager.sh": SH_FIXED}
        )
        self.assertEqual(verify.FAIL, proof["verdict"], proof)
        self.assertIn(f"{NO_TEST}, so the bug is not shown to be reproduced", " ".join(proof["failures"]))


if __name__ == "__main__":
    unittest.main()
