"""A plan guards existing behavior with the command that runs the existing tests, where no runner names them.

A refactor in a Ruby Minitest project (Claude models, 2026-11-13) was planned to change ``lib/pager.rb``
alone. Its proof failed twice with "No regression test was added or changed", the Resolver asked the person
to allow new tests in ``test/pager_test.rb``, and the run finished after 20 stages. A project whose tests are
named one by one guards such a change with its existing tests (``guard: test_x``); this one could not.
Owner decision, 2026-11-13: the existing tests' command must pass on the original code and with the change.

The proof cases use a shell-script project, which no runner detects, and a Makefile project, and run real
commands; no provider runs.
tools/autocode_command_guards.py
"""

from __future__ import annotations

import shutil
import unittest
from unittest import mock

import autocode_command_guards as command_guards
import autocode_goal_lifecycle as lifecycle
import autocode_plan_proof_command as plan_command
import autocode_plan_test_suite as plan_test_suite
import autocode_regression as regression
import autocode_test_cases as test_cases
import autocode_util as util
import autocode_verify as verify
from autocode_test_framework import Framework

from . import test_plan_test_suite as plan_tests
from .test_verify import Project

PAGER = "page_count() { echo $(( ($1 + $2 - 1) / $2 )); }\n"
# The same arithmetic with the rounding extracted into a helper: no behavior changes.
REFACTORED = 'round_up() { echo $(( ($1 + $2 - 1) / $2 )); }\npage_count() { round_up "$1" "$2"; }\n'
BROKEN = REFACTORED.replace("($1 + $2 - 1) / $2", "$1 / $2")
CHECK = (
    '. ./pager.sh\n[ "$(page_count 10 5)" = 2 ] || { echo "exact multiple"; exit 1; }\n'
    '[ "$(page_count 11 5)" = 3 ] || { echo "partial last page"; exit 1; }\n'
)
SEED = {"pager.sh": PAGER, "tests/test_pager.sh": CHECK}
GUARD = "guard: run `sh tests/test_pager.sh`"
PLAIN = "`sh tests/test_pager.sh` exits 0"


def contract(*methods, kind="build", status="approved"):
    rows = [{"id": f"AC{n}", "criterion": "pages", "verification_method": m} for n, m in enumerate(methods, 1)]
    sealed = {"task_id": "task-1", "revision": 1, "body": {"task_kind": kind, "acceptance_criteria": rows}}
    return {**sealed, "hash": util.digest(sealed), "approval_status": status}


class DeclarationTests(unittest.TestCase):
    def test_a_guard_that_starts_with_a_command_in_backticks_is_that_command(self):
        for method, command in (
            (GUARD, "sh tests/test_pager.sh"),
            ("Guard:  Run `ruby test/pager_test.rb` passes before and after the change", "ruby test/pager_test.rb"),
            ("guard: runs `make test` (the whole suite)", "make test"),
            # A test's name or title, which a plan may write in backticks too, is no command.
            ("guard: test_exact_multiple", None),
            ("guard: counts a partial last page", None),
            ("guard: `counts a partial last page` (tests/pager.test.js)", None),
            ("guard: `tests.test_seed.SeedTests.test_seed`", None),
            ("guard: `ruby test/pager_test.rb`", None),
            # Prose, a command that is not first, and a test: criterion are none either.
            ("guard: the suite `make test` keeps passing", None),
            ("guard: run the suite", None),
            ("guard: run `make test`and more", None),
            ("guard: run ``", None),
            ("test: run `sh tests/test_pager.sh`", None),
            (PLAIN, None),
            (None, None),
        ):
            with self.subTest(method=method):
                self.assertEqual(command, test_cases.guard_command(method))

    def test_a_command_guard_names_no_test_so_it_is_no_named_case(self):
        body = contract(GUARD, "guard: test_exact", "test: test_partial")["body"]
        self.assertEqual(["AC2", "AC3"], [case["id"] for case in test_cases.plan_cases(body)])
        self.assertEqual(
            [{"id": "AC1", "text": "pages", "command": "sh tests/test_pager.sh"}], command_guards.plan_guards(body)
        )
        self.assertEqual([], command_guards.plan_guards(None))

    def test_the_guards_command_is_one_of_the_approved_plans_commands(self):
        self.assertEqual(["sh tests/test_pager.sh"], plan_command.approved_commands(contract(GUARD)))
        self.assertEqual([], plan_command.approved_commands(contract(GUARD, status="draft")))

    def test_a_guard_runs_only_tests_the_original_code_has_and_only_where_none_is_named(self):
        tests = ["tests/test_pager.sh"]
        make, pytest = Framework("make", "make test"), Framework("pytest", "python -m pytest", runner="pytest")
        self.assertTrue(pytest.per_test)
        guard = lambda command: {"id": "AC1", "text": "", "command": command}  # noqa: E731
        for command, framework, runs in (
            ("sh tests/test_pager.sh", None, True),
            ("sh ./tests/test_pager.sh", make, True),
            ("make  test", make, True),  # the detected suite itself
            ("make test", None, False),
            ("true", None, False),
            ("sh pager.sh", make, False),
            ("sh tests/test_new.sh", None, False),
            ("sh tests/test_pager.sh", pytest, False),
        ):
            with self.subTest(command=command, framework=framework and framework.name):
                self.assertEqual(
                    [guard(command)] if runs else [], command_guards.usable([guard(command)], framework, tests)
                )

    def test_the_two_runs_of_a_guard_are_judged_by_their_exit_codes(self):
        guard = {"id": "AC2", "text": "", "command": "sh tests/test_pager.sh"}
        ran = lambda code, **more: {"exit_code": code, "timed_out": False, **more}  # noqa: E731
        self.assertEqual(([], []), command_guards.judge(guard, ran(0), ran(0)))
        failures, unverified = command_guards.judge(guard, ran(0), ran(1))
        self.assertEqual([], unverified)
        self.assertIn("passes on the original code and fails with the change (exit 1)", failures[0])
        failures, unverified = command_guards.judge(guard, ran(1), ran(0))
        self.assertIn("fails on the original code (exit 1), so it guards nothing", failures[0])
        failures, unverified = command_guards.judge(guard, ran(0), ran(None, timed_out=True))
        self.assertEqual([], failures)
        self.assertIn("did not finish with the change", unverified[0])
        self.assertIn("AC2 (`sh tests/test_pager.sh`)", command_guards.note([guard]))


class ProofTests(unittest.TestCase):
    def setUp(self):
        self.project = Project(SEED)
        self.addCleanup(self.project.close)
        self.run_dir = self.project.root.parent / "run"
        self.assertIsNone(verify.detect_framework(self.project.root))

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
            return regression.prove(state, self.project.root, self.run_dir)

    def test_a_refactor_is_proved_by_the_existing_tests_command_before_and_after(self):
        proof = self.prove(contract(GUARD), {"pager.sh": REFACTORED})
        self.assertEqual(verify.PASS, proof["verdict"], proof)
        self.assertEqual((["pager.sh"], []), (proof["source_files"], proof["test_files"]))
        checks = proof["checks"]
        self.assertEqual("sh tests/test_pager.sh", checks["guard_command_on_base:AC1"]["command"])
        self.assertEqual(
            (0, 0),
            (checks["guard_command_on_base:AC1"]["exit_code"], checks["guard_command_on_candidate:AC1"]["exit_code"]),
        )
        said = "the plan's guards AC1 (`sh tests/test_pager.sh`) rest on each command's exit code"
        self.assertTrue(any(said in note for note in proof["notes"]), proof["notes"])
        self.assertTrue(any(said in note for note in proof["review_reasons"]), proof["review_reasons"])

    def test_without_a_guard_the_same_refactor_still_needs_a_test(self):
        # The gate is where it was: a command that is no guard, a draft plan and a bug fix prove nothing here.
        for goal_contract in (contract(PLAIN), contract(GUARD, status="draft"), contract(GUARD, kind="bugfix")):
            with self.subTest(goal_contract=goal_contract["body"]["acceptance_criteria"][0]["verification_method"]):
                proof = self.prove(goal_contract, {"pager.sh": REFACTORED})
                self.assertNotEqual(verify.PASS, proof["verdict"], proof)
                if goal_contract["approval_status"] == "approved":
                    self.assertIn("No regression test was added or changed", " ".join(proof["failures"]))

    def test_a_change_that_breaks_the_guarded_tests_is_refused(self):
        proof = self.prove(contract(GUARD), {"pager.sh": BROKEN})
        self.assertEqual(verify.FAIL, proof["verdict"], proof)
        self.assertIn(
            "Guard AC1's command `sh tests/test_pager.sh` passes on the original code and fails with the change",
            " ".join(proof["failures"]),
        )

    def test_a_guard_that_fails_on_the_original_code_guards_nothing(self):
        broken = Project({"pager.sh": BROKEN, "tests/test_pager.sh": CHECK})
        self.addCleanup(broken.close)
        self.project, self.run_dir = broken, broken.root.parent / "run"
        proof = self.prove(contract(GUARD), {"pager.sh": REFACTORED})
        self.assertEqual(verify.FAIL, proof["verdict"], proof)
        self.assertIn("fails on the original code (exit 1), so it guards nothing", " ".join(proof["failures"]))

    def test_a_guard_beside_a_changed_test_file_leaves_the_ordinary_proof_in_charge(self):
        added = CHECK + '[ "$(page_count 1 5)" = 1 ] || { echo "one page"; exit 1; }\n'
        run = "`sh tests/test_pager.sh` fails on the original code and passes with the change"
        # The new check passes on the original code too, so the changed test proves nothing: the guard does not
        # make a change with tests coverage.
        proof = self.prove(contract(run, GUARD), {"pager.sh": REFACTORED, "tests/test_pager.sh": added})
        self.assertEqual(verify.FAIL, proof["verdict"], proof)
        self.assertIn("also pass on the unfixed base code", " ".join(proof["failures"]))
        # The guard itself ran on the original code, with the original test file, and held.
        self.assertEqual(0, proof["checks"]["guard_command_on_base:AC2"]["exit_code"])

    def test_a_guard_that_runs_no_existing_test_is_not_run(self):
        for method in ("guard: run `true`", "guard: run `sh pager.sh`"):
            with self.subTest(method=method):
                proof = self.prove(contract(method), {"pager.sh": REFACTORED})
                self.assertNotEqual(verify.PASS, proof["verdict"], proof)
                self.assertNotIn("guard_command_on_base:AC1", proof.get("checks") or {})


@unittest.skipUnless(shutil.which("make"), "the project's suite is make test")
class DetectedSuiteTests(unittest.TestCase):
    def test_the_detected_suite_is_a_guard_too(self):
        project = Project({**SEED, "Makefile": "test:\n\tsh tests/test_pager.sh\n\n.PHONY: test\n"})
        self.addCleanup(project.close)
        self.assertEqual("make test", verify.detect_framework(project.root).suite)
        project.write({"pager.sh": REFACTORED})
        state = {
            "workspace": str(project.root),
            "base_commit": project.base,
            "iteration": 1,
            "stages": [],
            "history": [],
            "settings": {"regression": {}},
            "goal_contract": contract("guard: run `make test`"),
        }
        with mock.patch.object(verify.diff_coverage, "collect", return_value=None):
            proof = regression.prove(state, project.root, project.root.parent / "run")
        self.assertEqual(verify.PASS, proof["verdict"], proof)
        self.assertEqual("make test", proof["checks"]["guard_command_on_candidate:AC1"]["command"])


class PlanCheckTests(unittest.TestCase):
    """A command guard the proof could never run goes back to the Planner when the plan is drafted."""

    def state(self, files):
        project = Project(files)
        self.addCleanup(project.close)
        return plan_tests.CheckTests.state(self, project)

    def body(self, method, *paths):
        body = plan_tests.plan(*paths)
        body["acceptance_criteria"][0]["verification_method"] = method
        return body

    def test_a_guard_on_the_existing_test_file_is_accepted_with_no_test_file_planned(self):
        lifecycle.validate_body(self.state(SEED), self.body(GUARD), ready=True)

    def test_a_guard_that_runs_no_existing_test_is_refused(self):
        refusal = r"A guard's command must run tests the project already has.*C1 `sh tests/test_new.sh` names no"
        with self.assertRaisesRegex(ValueError, refusal):
            lifecycle.validate_body(self.state(SEED), self.body("guard: run `sh tests/test_new.sh`"), ready=True)

    def test_a_project_whose_runner_names_its_tests_keeps_naming_them(self):
        python = {"pager.py": "def page_count(total, size):\n    return total // size\n", "tests/test_pager.py": ""}
        with self.assertRaisesRegex(ValueError, r"Criteria C1 guard with a command, but this project's \w+ suite"):
            lifecycle.validate_body(
                self.state(python), self.body("guard: run `python -m unittest tests.test_pager`"), ready=True
            )

    def test_the_planner_is_told_how_to_guard_where_no_test_is_named(self):
        said = plan_test_suite.unnamed_cases(
            None, Framework("make", "make test"), [], self.body("guard: test_exact"), lambda directory: None
        )
        self.assertIn(
            'write "guard: run `ruby test/pager_test.rb`", the command that runs its existing test file', said
        )


if __name__ == "__main__":
    unittest.main()
