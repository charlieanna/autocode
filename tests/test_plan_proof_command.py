"""With no test runner AutoCode knows, the proof runs the approved plan's own test command.

A live Ruby Minitest bug fix (Claude models, 2026-11-13) ended with the proof ``UNVERIFIED`` ("No
project test command was found ... pass --test-command"), and the Resolver asked the person to type
``--test-command 'ruby test/pager_test.rb'``, the very command the approved plan already ran. Any
language or layout ``autocode_toolchain_suite.detect`` does not list met the same question. The proof
cases here use a shell-script project, which nothing detects, and real commands; no provider runs.
tools/autocode_plan_proof_command.py
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

import autocode_plan_proof_command as plan_command
import autocode_protected_oracles as protected
import autocode_regression as regression
import autocode_util as util
import autocode_verify as verify

from .test_verify import Project

PAGER = "page_count() { echo $(( $1 / $2 )); }\n"
FIXED = "page_count() { echo $(( ($1 + $2 - 1) / $2 )); }\n"
CHECK = '. ./pager.sh\n[ "$(page_count 10 5)" = 2 ] || { echo "exact multiple"; exit 1; }\n'
REGRESSION = CHECK + '[ "$(page_count 11 5)" = 3 ] || { echo "partial last page"; exit 1; }\n'
SEED = {"pager.sh": PAGER, "tests/test_pager.sh": CHECK}
RUN = "`sh tests/test_pager.sh` fails on the original code and passes with the fix"


def contract(*methods, status="approved"):
    rows = [{"id": f"AC{n}", "criterion": "pages", "verification_method": m} for n, m in enumerate(methods, 1)]
    sealed = {"task_id": "task-1", "revision": 1, "body": {"task_kind": "bugfix", "acceptance_criteria": rows}}
    return {**sealed, "hash": util.digest(sealed), "approval_status": status}


class ChoiceTests(unittest.TestCase):
    def test_only_an_approved_plans_commands_count(self):
        self.assertEqual(["sh tests/test_pager.sh"], plan_command.approved_commands(contract(RUN)))
        self.assertEqual([], plan_command.approved_commands(contract(RUN, status="draft")))
        self.assertEqual([], plan_command.approved_commands(None))
        reviewed = contract(RUN)
        reviewed["body"]["acceptance_criteria"][0]["human_review"] = True
        self.assertEqual([], plan_command.approved_commands(reviewed))

    def test_the_first_command_that_runs_a_test_file_takes_the_roles_it_can(self):
        tests = {"original_tests": ["tests/test_pager.sh"], "changed_tests": ["tests/test_pager.sh"]}
        chosen = plan_command.choose(["sh pager.sh", "sh ./tests/test_pager.sh"], **tests)
        self.assertEqual(
            {"command": "sh ./tests/test_pager.sh", "suite": "sh ./tests/test_pager.sh", "regression": None}
            | {"regression": "sh ./tests/test_pager.sh"},
            chosen,
        )
        # A new test file is no suite: it does not exist in the original code, so it cannot pass there.
        added = plan_command.choose(["sh tests/test_new.sh"], original_tests=[], changed_tests=["tests/test_new.sh"])
        self.assertEqual(
            {"command": "sh tests/test_new.sh", "suite": None, "regression": "sh tests/test_new.sh"}, added
        )
        # An unchanged original test file is the suite only.
        kept = plan_command.choose(["sh tests/test_pager.sh"], original_tests=["tests/test_pager.sh"], changed_tests=[])
        self.assertEqual(
            {"command": "sh tests/test_pager.sh", "suite": "sh tests/test_pager.sh", "regression": None}, kept
        )
        self.assertIsNone(plan_command.choose(["sh pager.sh", "true"], **tests))
        self.assertIn(
            "`sh tests/test_pager.sh` as the suite and the regression command",
            plan_command.note(chosen | {"command": "sh tests/test_pager.sh"}),
        )


class ProofTests(unittest.TestCase):
    def setUp(self):
        self.project = Project(SEED)
        self.addCleanup(self.project.close)
        self.run_dir = self.project.root.parent / "run"
        self.assertIsNone(verify.detect_framework(self.project.root))

    def prove(self, goal_contract):
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

    def test_a_fix_is_proven_by_the_approved_plans_command(self):
        self.project.write({"pager.sh": FIXED, "tests/test_pager.sh": REGRESSION})
        proof = self.prove(contract(RUN))
        self.assertEqual(verify.PASS, proof["verdict"], proof)
        self.assertEqual("sh tests/test_pager.sh", proof["commands"]["regression"])
        self.assertEqual("sh tests/test_pager.sh", proof["commands"]["suite"])
        reason = (
            "AutoCode found no test runner it reads in this project, so it ran the approved plan's command "
            "`sh tests/test_pager.sh` as the suite and the regression command"
        )
        self.assertTrue(any(note.startswith(reason) for note in proof["notes"]), proof["notes"])
        self.assertTrue(any(note.startswith(reason) for note in proof["review_reasons"]), proof["review_reasons"])

    def test_a_test_the_original_code_passes_still_fails_the_proof(self):
        self.project.write(
            {"pager.sh": FIXED, "tests/test_pager.sh": CHECK + '[ "$(page_count 5 5)" = 1 ] || exit 1\n'}
        )
        proof = self.prove(contract(RUN))
        self.assertEqual(verify.FAIL, proof["verdict"], proof)
        self.assertIn("The regression tests also pass on the unfixed base code", " ".join(proof["failures"]))

    def test_without_a_command_in_the_approved_plan_nothing_is_guessed(self):
        self.project.write({"pager.sh": FIXED, "tests/test_pager.sh": REGRESSION})
        for goal_contract in (contract("Run the pager by hand"), contract(RUN, status="draft")):
            with self.subTest(goal_contract=goal_contract["approval_status"]):
                proof = self.prove(goal_contract)
                self.assertEqual(verify.UNVERIFIED, proof["verdict"], proof)
                self.assertFalse(proof.get("commands", {}).get("suite"))


class ProtectedTestTests(unittest.TestCase):
    """The original-test gate binds the project's tests at launch, before any plan exists, so with no runner
    detected it bound no command. A live shell bug fix (2026-11-13) then stopped with "No original suite
    command was available; explicit user revision required", a question about machinery."""

    def setUp(self):
        self.project = Project(SEED)
        self.addCleanup(self.project.close)
        self.run_dir = self.project.root.parent / "run"
        self.state = {"settings": {}, "workspace": str(self.project.root)}
        protected.reconcile(
            self.state,
            self.state["settings"],
            SimpleNamespace(run_dir=None),
            self.project.root,
            self.run_dir,
            is_test_path=verify.is_test_path,
            discover_command=lambda: None,
        )
        self.assertIsNone(self.state["settings"]["protected_tests"]["command"])

    def replay(self, goal_contract):
        self.state["goal_contract"] = goal_contract
        return protected.replay(self.state, self.project.root, self.run_dir / "replays", verify.scratch_run, timeout=30)

    def test_the_original_tests_run_with_the_approved_plans_command(self):
        self.project.write({"pager.sh": FIXED, "tests/test_pager.sh": REGRESSION})
        result = self.replay(contract(RUN))
        self.assertEqual("PASS", result["verdict"], result)
        self.assertEqual("sh tests/test_pager.sh", result["plan_command"])
        self.assertEqual(0, result["original"]["exit_code"])

    def test_a_weakened_original_test_still_fails_the_gate(self):
        # The fix breaks the original exact-multiple case, and the edited test hides it.
        self.project.write({"pager.sh": "page_count() { echo 3; }\n", "tests/test_pager.sh": ". ./pager.sh\n"})
        with self.assertRaisesRegex(ValueError, "original gate did not pass"):
            self.replay(contract(RUN))

    def test_without_an_approved_command_the_gate_still_cannot_run(self):
        self.project.write({"pager.sh": FIXED, "tests/test_pager.sh": REGRESSION})
        with self.assertRaisesRegex(ValueError, "No original suite command was available"):
            self.replay(contract("Run the pager by hand"))


if __name__ == "__main__":
    unittest.main()
