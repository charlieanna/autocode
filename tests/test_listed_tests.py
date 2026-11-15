"""A change's new test that runs only because another file of the change lists it.

The regression proof runs the change's tests on the original code, and takes "the change's tests" to be its
test files. A project whose ``Makefile``, ``package.json`` script or ``run_tests.sh`` names each test runs a
new one only once that file is edited, so on the original code the test command never ran the new test and
passed: "The new tests pass on the unfixed base code, so they do not reproduce the bug". A real feature in
a C project whose ``Makefile`` lists each test program (2026-11-15) failed its proof twice that way and
asked the person to set a regression command; with that command set, the same change fails again with "The
base suite definition fails against the candidate code".

The pure cases use a scripted ``look``. The proof cases run real commands in a shell project whose runner
lists each test file and in a ``make`` project; no provider runs.
tools/autocode_listed_tests.py
"""

from __future__ import annotations

import shutil
import unittest

import autocode_listed_tests as listed

from . import test_plan_command_roles as t

PASSED_ON_BASE = "The new tests pass on the unfixed base code, so they do not reproduce the bug"
DEFINITION_FAILED = (
    "The base suite definition fails against the candidate code: the candidate changed which tests the "
    "suite runs, so tests the base ran no longer pass"
)
# A runner that names each test file it runs.
LISTING = '#!/bin/sh\nset -e\ncd "$(dirname "$0")"\nsh tests/test_pager.sh\n'
SEED = {"pager.sh": t.BUG, "tests/test_pager.sh": t.CHECK, "run_tests.sh": LISTING}
NEW_TEST = ". ./pager.sh\n" + t.PARTIAL
LISTED = LISTING + "sh tests/test_partial.sh\n"
MAKEFILE = ".PHONY: test\ntest:\n\tsh tests/test_pager.sh\n"
MAKE_SEED = {"pager.sh": t.BUG, "tests/test_pager.sh": t.CHECK, "Makefile": MAKEFILE}
MAKE_LISTED = MAKEFILE + "\tsh tests/test_partial.sh\n"


def failed(reason, *, check="regression_on_base", exit_code=0, results=None, command="./run_tests.sh"):
    return {
        "verdict": "FAIL",
        "failures": [reason],
        "checks": {check: {"exit_code": exit_code, "timed_out": False, "results": results, "command": command}},
        "commands": {"suite": "make test", "regression": command},
    }


class Look:
    """A scripted ``look``: the exit code for each (reverted, unloadable), and what was asked."""

    def __init__(self, answer):
        self.answer, self.asked = answer, []

    def __call__(self, reverted, unloadable):
        self.asked.append((reverted, unloadable))
        return self.answer(set(reverted), set(unloadable))


class WantedTests(unittest.TestCase):
    def test_a_command_that_passed_on_the_original_code_by_exit_code_is_looked_at(self):
        self.assertEqual(listed.wanted(failed(PASSED_ON_BASE)), ("./run_tests.sh", listed.PASSED))

    def test_the_original_listing_failing_with_the_change_is_looked_at_with_the_suite(self):
        result = failed(DEFINITION_FAILED, check="suite_base_definition_on_candidate", exit_code=2, command="make test")
        self.assertEqual(listed.wanted(result), ("make test", listed.DEFINITION))

    def test_a_runner_that_names_its_tests_says_which_ran(self):
        named = {"passed": ["test_exact"], "failed": []}
        self.assertIsNone(listed.wanted(failed(PASSED_ON_BASE, results=named)))
        result = failed(DEFINITION_FAILED, check="suite_base_definition_on_candidate", exit_code=1, results=named)
        self.assertIsNone(listed.wanted(result))

    def test_only_a_proof_that_failed_for_that_reason_alone(self):
        self.assertIsNone(listed.wanted({**failed(PASSED_ON_BASE), "verdict": "PASS", "failures": []}))
        self.assertIsNone(listed.wanted({**failed(PASSED_ON_BASE), "failures": [PASSED_ON_BASE, "The suite fails"]}))
        self.assertIsNone(listed.wanted(failed("The suite fails with the change")))
        self.assertIsNone(listed.wanted(failed(PASSED_ON_BASE, exit_code=1)))


class FindTests(unittest.TestCase):
    def test_the_file_whose_original_stops_the_tests_running_lists_them(self):
        # The original code runs neither test; with the change the command fails without them, unless
        # the Makefile is put back.
        look = Look(lambda reverted, unloadable: 0 if "Makefile" in reverted else 2)
        files, unrun, seen = listed.find(["tests/b.c"], ["Makefile", "clamp.c"], look)
        self.assertEqual((files, unrun), (["Makefile"], ["tests/b.c"]))
        self.assertEqual(
            seen,
            {"original_without_tests": 0, "change_without_tests": 2, "without:Makefile": 0, "without:clamp.c": 2},
        )
        self.assertEqual(look.asked[0], (("Makefile", "clamp.c"), ("tests/b.c",)))

    def test_one_test_that_runs_on_the_original_code_costs_one_look(self):
        look = Look(lambda reverted, unloadable: 1)
        self.assertEqual(listed.find(["tests/b.sh"], ["Makefile"], look)[:2], ([], []))
        self.assertEqual(len(look.asked), 1)

    def test_a_command_that_runs_the_tests_nowhere_names_them_and_looks_no_further(self):
        look = Look(lambda reverted, unloadable: 0)
        files, unrun, seen = listed.find(["tests/b.sh"], ["Makefile", "pager.sh"], look)
        self.assertEqual((files, unrun), ([], ["tests/b.sh"]))
        self.assertTrue(listed.unrun(seen))
        self.assertEqual(len(look.asked), 2)

    def test_a_test_the_original_code_runs_is_left_out_and_the_others_are_looked_for(self):
        # tests/a.sh is an existing test the change edits: the original listing runs it. tests/b.sh is new.
        def answer(reverted, unloadable):
            if "tests/a.sh" in unloadable:
                return 1
            return 0 if "Makefile" in reverted else 1

        look = Look(answer)
        files, unrun, seen = listed.find(["tests/a.sh", "tests/b.sh"], ["Makefile", "pager.sh"], look)
        self.assertEqual((files, unrun), (["Makefile"], ["tests/b.sh"]))
        self.assertEqual(seen["original_without:tests/a.sh"], 1)
        self.assertEqual(seen["original_without:tests/b.sh"], 0)
        self.assertEqual(look.asked[-1], (("pager.sh",), ("tests/b.sh",)))

    def test_every_test_running_on_the_original_code_ends_the_looks(self):
        look = Look(lambda reverted, unloadable: 1)
        self.assertEqual(listed.find(["tests/a.sh", "tests/b.sh"], ["Makefile"], look)[:2], ([], []))
        self.assertEqual(len(look.asked), 3)

    def test_the_looks_are_bounded(self):
        others = [f"src/{n}.c" for n in range(listed.MAX_FILES + 3)]
        look = Look(lambda reverted, unloadable: 0 if len(reverted) == len(others) else 2)
        self.assertEqual(listed.find(["tests/b.c"], others, look)[0], [])
        self.assertEqual(len(look.asked), 2 + listed.MAX_FILES)
        many = [f"tests/{n}.c" for n in range(listed.MAX_FILES + 1)]
        look = Look(lambda reverted, unloadable: 1)
        self.assertEqual(listed.find(many, ["Makefile"], look)[:2], ([], []))
        self.assertEqual(len(look.asked), 1)

    def test_a_run_that_does_not_finish_shows_nothing(self):
        look = Look(lambda reverted, unloadable: None)
        self.assertEqual(listed.find(["tests/b.sh"], ["Makefile"], look)[:2], ([], []))
        look = Look(lambda reverted, unloadable: 0 if reverted else None)
        files, unrun, seen = listed.find(["tests/b.sh"], ["Makefile"], look)
        self.assertEqual((files, unrun, listed.unrun(seen)), ([], ["tests/b.sh"], False))

    def test_nothing_to_look_at(self):
        look = Look(lambda reverted, unloadable: 0)
        self.assertEqual(listed.find([], ["Makefile"], look), ([], [], {}))
        self.assertEqual(listed.find(["tests/b.sh"], [], look), ([], [], {}))
        self.assertEqual(look.asked, [])

    def test_the_notes_name_the_command_the_tests_and_the_files(self):
        one = listed.note("make test", ["tests/b.c"], ["Makefile"])
        self.assertIn("Makefile, which the change edits, lists the change's tests tests/b.c", one)
        self.assertIn("`make test` fails when they cannot be loaded", one)
        self.assertIn("So Makefile is taken with the tests", one)
        two = listed.note("npm test", ["test/b.js"], ["package.json", "run.sh"])
        self.assertIn("package.json, run.sh, which the change edits, list the change's tests", two)
        self.assertIn("So package.json, run.sh are taken with the tests", two)
        self.assertIn(
            "`make test` passes with the change even when tests/b.c cannot be loaded: it does not run them",
            listed.unrun_note("make test", ["tests/b.c"]),
        )


class ProofTests(unittest.TestCase):
    """Real proofs in a shell project whose runner names each test file."""

    FLIP = t.contract(t.FLIPS.format("./run_tests.sh"))

    def test_a_bug_fix_whose_new_test_file_the_runner_lists_is_proven(self):
        change = {"pager.sh": t.FIXED, "tests/test_partial.sh": NEW_TEST, "run_tests.sh": LISTED}
        proof = t.prove(self, self.FLIP, change, seed=SEED)
        self.assertEqual((proof["verdict"], proof["failures"], proof["unverified"]), ("PASS", [], []))
        seen = proof["listed_tests"]
        self.assertEqual((seen["files"], seen["tests"]), (["run_tests.sh"], ["tests/test_partial.sh"]))
        self.assertEqual((seen["command"], seen["reason"]), ("./run_tests.sh", listed.PASSED))
        self.assertEqual(seen["seen"]["without:run_tests.sh"], 0)
        self.assertNotEqual(seen["seen"]["without:pager.sh"], 0)
        note = "run_tests.sh, which the change edits, lists the change's tests tests/test_partial.sh"
        self.assertTrue(any(note in said for said in proof["notes"]), proof["notes"])
        self.assertTrue(any(note in said for said in proof["review_reasons"]), proof["review_reasons"])

    def test_a_feature_in_a_new_file_with_its_own_listed_test_is_proven(self):
        change = {
            "page_of.sh": "page_of() { echo $(( $1 / $2 + 1 )); }\n",
            "tests/test_page_of.sh": '. ./page_of.sh\n[ "$(page_of 5 5)" = 2 ] || exit 1\n',
            "run_tests.sh": LISTING + "sh tests/test_page_of.sh\n",
        }
        seed = {**SEED, "pager.sh": t.FIXED}
        proof = t.prove(self, t.contract(t.FLIPS.format("./run_tests.sh"), kind="build"), change, seed=seed)
        self.assertEqual((proof["verdict"], proof["failures"], proof["unverified"]), ("PASS", [], []))
        self.assertEqual(proof["listed_tests"]["files"], ["run_tests.sh"])

    def test_an_existing_test_the_change_also_edits_does_not_hide_the_listed_one(self):
        change = {
            "pager.sh": t.FIXED,
            "tests/test_pager.sh": "# exact multiples\n" + t.CHECK,
            "tests/test_partial.sh": NEW_TEST,
            "run_tests.sh": LISTED,
        }
        proof = t.prove(self, self.FLIP, change, seed=SEED)
        self.assertEqual((proof["verdict"], proof["failures"], proof["unverified"]), ("PASS", [], []))
        seen = proof["listed_tests"]
        self.assertEqual((seen["files"], seen["tests"]), (["run_tests.sh"], ["tests/test_partial.sh"]))

    def test_a_listed_test_that_passes_on_the_original_code_still_fails_the_proof(self):
        # The new test checks what already works: run on the original code with the runner that lists it,
        # it passes there, and that is what the proof says.
        change = {
            "pager.sh": t.FIXED,
            "tests/test_again.sh": t.CHECK,
            "run_tests.sh": LISTING + "sh tests/test_again.sh\n",
        }
        proof = t.prove(self, self.FLIP, change, seed=SEED)
        self.assertEqual((proof["verdict"], proof["failures"]), ("FAIL", [PASSED_ON_BASE]))
        self.assertEqual(proof["listed_tests"]["files"], ["run_tests.sh"])

    def test_a_new_test_file_nothing_runs_fails_the_proof_and_says_so(self):
        change = {"pager.sh": t.FIXED, "tests/test_partial.sh": NEW_TEST}
        proof = t.prove(self, self.FLIP, change, seed=SEED)
        self.assertEqual((proof["verdict"], proof["failures"]), ("FAIL", [PASSED_ON_BASE]))
        self.assertEqual(proof["listed_tests"]["files"], [])
        said = "`./run_tests.sh` passes with the change even when tests/test_partial.sh cannot be loaded"
        self.assertTrue(any(said in note for note in proof["notes"]), proof["notes"])

    def test_a_test_the_original_runner_already_runs_is_not_looked_for(self):
        # A runner that runs every test file: the new one ran on the original code and passed there.
        seed = {**SEED, "run_tests.sh": t.RUNNER}
        change = {"pager.sh": t.FIXED, "tests/test_again.sh": t.CHECK, "run_tests.sh": t.RUNNER + "# all\n"}
        proof = t.prove(self, self.FLIP, change, seed=seed)
        self.assertEqual((proof["verdict"], proof["failures"]), ("FAIL", [PASSED_ON_BASE]))
        self.assertEqual(proof["listed_tests"]["files"], [])
        self.assertEqual(list(proof["listed_tests"]["seen"]), ["original_without_tests"])

    def test_a_runner_edited_to_drop_a_test_the_change_breaks_still_fails(self):
        # The change breaks exact multiples and lists only its new test. Put back, the original runner
        # runs the old test, which fails with the change: the runner is not taken with the tests.
        broken = "page_count() { echo $(( ($1 + $2) / $2 )); }\n"
        only_new = LISTED.replace("sh tests/test_pager.sh\n", "")
        change = {"pager.sh": broken, "tests/test_partial.sh": NEW_TEST, "run_tests.sh": only_new}
        proof = t.prove(self, self.FLIP, change, seed=SEED)
        self.assertEqual(proof["verdict"], "FAIL")
        self.assertEqual(proof["listed_tests"]["files"], [])


@unittest.skipUnless(shutil.which("make"), "the project's suite is make test")
class DetectedSuiteTests(unittest.TestCase):
    """Real proofs where AutoCode detects the suite (``make test``) and the Makefile names each test."""

    FLIP = t.contract(t.FLIPS.format("make test"))
    CHANGE = {"pager.sh": t.FIXED, "tests/test_partial.sh": NEW_TEST, "Makefile": MAKE_LISTED}

    def test_a_bug_fix_whose_new_test_the_makefile_lists_is_proven(self):
        proof = t.prove(self, self.FLIP, self.CHANGE, seed=MAKE_SEED)
        self.assertEqual((proof["verdict"], proof["failures"], proof["unverified"]), ("PASS", [], []))
        self.assertEqual(proof["listed_tests"]["files"], ["Makefile"])
        self.assertEqual(proof["listed_tests"]["reason"], listed.PASSED)

    def test_the_original_tests_run_with_the_change_as_the_original_makefile_lists_them(self):
        # With the regression command the first failure asked the person to set, the proof went on to run
        # the change's Makefile without the change's tests, and failed on the test file that tree lacks.
        state = {}
        made = t.project(self, MAKE_SEED)
        made.write(self.CHANGE)
        state.update(t.run_state(made, t.contract("a partial last page counts")))
        state["settings"]["regression"]["regression_command"] = "sh tests/test_partial.sh"
        with t.mock.patch.object(t.verify.diff_coverage, "collect", return_value=None):
            proof = t.regression.prove(state, made.root, made.root.parent / "run")
        self.assertEqual((proof["verdict"], proof["failures"], proof["unverified"]), ("PASS", [], []))
        self.assertEqual(proof["listed_tests"]["files"], ["Makefile"])
        self.assertEqual(proof["listed_tests"]["reason"], listed.DEFINITION)

    def test_a_makefile_that_drops_a_test_the_change_breaks_fails_on_the_original_listing(self):
        # The same, with a change that breaks exact multiples and a Makefile that no longer runs their test.
        broken = "page_count() { echo $(( ($1 + $2) / $2 )); }\n"
        only_new = ".PHONY: test\ntest:\n\tsh tests/test_partial.sh\n"
        state = {}
        made = t.project(self, MAKE_SEED)
        made.write({**self.CHANGE, "pager.sh": broken, "Makefile": only_new})
        state.update(t.run_state(made, t.contract("a partial last page counts")))
        state["settings"]["regression"]["regression_command"] = "sh tests/test_partial.sh"
        with t.mock.patch.object(t.verify.diff_coverage, "collect", return_value=None):
            proof = t.regression.prove(state, made.root, made.root.parent / "run")
        self.assertEqual(proof["verdict"], "FAIL")
        self.assertEqual(proof["failures"], [DEFINITION_FAILED])
        self.assertEqual(proof["listed_tests"]["files"], [])


if __name__ == "__main__":
    unittest.main()
