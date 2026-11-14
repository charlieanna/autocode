"""A guard the change itself alters goes back to plan review as a test: that replaces the original test.

A real run (Claude models, 2026-11-14) of "the report prints the joining date as 4/3/2026; print it as
2026-03-04" in a project whose expected report is a golden file was planned with
``guard: test_report_matches_the_golden_file``. The change rewrites the golden file, so with the change's
test files that test fails on the original code, and the proof refused the guard twice. A Tester, a
Completion Reviewer and a Resolver then each reported the same thing, and the person was asked "How should
AC5 be classified?". The code and the tests were right.

These cases run real regression proofs and the real original-test gate in a Git project; no provider runs.
tools/autocode_superseded_correction.py (guard_proposal, relabel_guards)
"""

from __future__ import annotations

import copy
import shlex
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import autocode_completion_result as completion_result
import autocode_contract_identity as contracts
import autocode_protected_oracles as guard
import autocode_regression as regression
import autocode_superseded_correction as correction
import autocode_util as util
import autocode_verify as verify

from .test_verify import Project

REPORT = (
    '"""The weekly report."""\n\n\ndef render(rows):\n'
    '    """One line per person: the name and the date they joined."""\n'
    "    return ''.join(f'{name}: {day}/{month}/{year}\\n' for name, (year, month, day) in rows)\n"
)
REPORT_NEW = REPORT.replace("{day}/{month}/{year}", "{year:04d}-{month:02d}-{day:02d}")
TESTS = (
    "import unittest\nfrom pathlib import Path\n\nfrom report import render\n\n"
    "GOLDEN = Path(__file__).with_name('golden')\nROWS = [('ann', (2026, 3, 4)), ('bob', (2025, 12, 31))]\n\n\n"
    "class ReportTests(unittest.TestCase):\n    def test_report_matches_the_golden_file(self):\n"
    "        self.assertEqual((GOLDEN / 'report.txt').read_text(), render(ROWS))\n\n"
    "    def test_no_rows_is_an_empty_report(self):\n        self.assertEqual('', render([]))\n"
)
PADDED = (
    "\n    def test_single_digits_are_padded(self):\n"
    "        self.assertEqual('ann: 2026-03-04\\n', render([('ann', (2026, 3, 4))]))\n"
)
SEED = {
    "report.py": REPORT,
    "tests/__init__.py": "",
    "tests/test_report.py": TESTS,
    "tests/golden/report.txt": "ann: 4/3/2026\nbob: 31/12/2025\n",
}
# What the real run's Builder delivered: the code, the golden file and a new test beside the old ones.
CHANGE = {
    "report.py": REPORT_NEW,
    "tests/test_report.py": TESTS + PADDED,
    "tests/golden/report.txt": "ann: 2026-03-04\nbob: 2025-12-31\n",
}
GOLDEN = "test_report_matches_the_golden_file"
ENTRY = {"file": "tests/test_report.py", "test": GOLDEN}
PLAN = [
    {
        "id": "AC1",
        "criterion": "single digits are padded",
        "verification_method": "test: test_single_digits_are_padded",
    },
    {
        "id": "AC2",
        "criterion": "no rows is an empty report",
        "verification_method": "guard: test_no_rows_is_an_empty_report",
    },
    {"id": "AC3", "criterion": "the report equals the golden file", "verification_method": f"guard: {GOLDEN}"},
]


class Run(unittest.TestCase):
    def setUp(self):
        self.project = Project(SEED)
        self.addCleanup(self.project.close)
        self.root = self.project.root
        self.run_dir = self.root.parent / "run"
        self.state = {
            "workspace": str(self.root),
            "base_commit": self.project.base,
            "iteration": 1,
            "stages": [],
            "history": [],
            "settings": {"regression": {}},
            "user_events": [],
        }
        command = shlex.join([sys.executable, "-m", "unittest", "discover", "-v"])
        guard.reconcile(
            self.state,
            self.state["settings"],
            SimpleNamespace(run_dir=None),
            self.root,
            self.run_dir,
            is_test_path=verify.is_test_path,
            discover_command=lambda: command,
        )

    def approve(self, rows, revision=1):
        body = {"acceptance_criteria": copy.deepcopy(rows), "open_blocking_questions": []}
        contract = {"task_id": "t", "revision": revision, "body": body}
        contract["hash"] = util.digest(contract)
        event = {"kind": "goal_approval", "actor": "user_cli", "token": contracts.token(contract)}
        contract.update(approval_status="approved", approval_event=event, origin="planner_draft")
        self.state["goal_contract"] = contract
        self.state["user_events"] = [event]

    def prove(self, change=None):
        self.project.write(CHANGE if change is None else change)
        with mock.patch.object(verify.diff_coverage, "collect", return_value=None):
            return regression.prove(self.state, self.root, self.run_dir)


class ProposalTests(Run):
    def test_the_guard_becomes_a_test_that_replaces_the_original_and_both_gates_then_pass(self):
        self.approve(PLAN)
        proof = self.prove()
        self.assertEqual(verify.FAIL, proof["verdict"], proof)
        self.assertEqual(1, len(proof["failures"]), proof["failures"])
        self.assertIn("Preserve case AC3", proof["failures"][0])
        self.assertIn(regression.CHANGED_GUARD, proof["failures"][0])
        self.assertEqual([{"id": "AC3", "tests": [f"tests.test_report.ReportTests.{GOLDEN}"]}], proof["changed_guards"])
        found = correction.guard_proposal(self.state)
        self.assertEqual([{"criterion": "AC3", **ENTRY}], found["tests"])
        rows = {row["id"]: row for row in found["body"]["acceptance_criteria"]}
        self.assertEqual(f"test: {GOLDEN}", rows["AC3"]["verification_method"])
        self.assertEqual([ENTRY], rows["AC3"]["supersedes"])
        # The other criteria are as approved, and so is the approved plan itself until plan review and approval.
        self.assertEqual(PLAN[:2], found["body"]["acceptance_criteria"][:2])
        self.assertEqual(PLAN, self.state["goal_contract"]["body"]["acceptance_criteria"])
        self.assertEqual(
            [("AC3", "reworded", "runner_proof", "")],
            [(row["item"], row["change"], row["basis"], row["answer_id"]) for row in found["changes"]],
        )
        self.assertIn(f"superseding tests/test_report.py::{GOLDEN}", found["changes"][0]["replacement"])
        # Once that plan is approved, the same change passes the proof, with the golden test failing first,
        self.approve(found["body"]["acceptance_criteria"], revision=2)
        proof = self.prove()
        self.assertEqual(verify.PASS, proof["verdict"], proof)
        self.assertNotIn("changed_guards", proof)
        self.assertEqual([f"tests.test_report.ReportTests.{GOLDEN}"], proof["case_tests"]["AC3"])
        self.assertIn(f"tests.test_report.ReportTests.{GOLDEN}", proof["fail_to_pass"])
        # and the original tests' gate, where the original golden test is the one failure it accepts.
        result = guard.replay(self.state, self.root, self.run_dir / "replays", verify.scratch_run, timeout=60)
        self.assertEqual("PASS", result["verdict"])
        self.assertEqual([f"tests.test_report.ReportTests.{GOLDEN}"], result["superseded"]["excused_failures"])

    def test_a_proof_that_failed_for_anything_else_too_is_left_alone(self):
        # The new test passes on the original code as well, so the proof has a second failure.
        self.approve(PLAN)
        weak = PADDED.replace("'ann: 2026-03-04\\n', render([('ann', (2026, 3, 4))])", "'', render([])")
        proof = self.prove({**CHANGE, "tests/test_report.py": TESTS + weak})
        self.assertEqual(verify.FAIL, proof["verdict"], proof)
        self.assertEqual(["AC3"], [row["id"] for row in proof["changed_guards"]])
        self.assertGreater(len(proof["failures"]), 1, proof["failures"])
        self.assertIsNone(correction.guard_proposal(self.state))

    def test_a_guard_that_holds_is_no_conflict(self):
        # The golden file is left alone: nothing but the new test fails on the original code.
        self.approve(PLAN[:2])
        proof = self.prove({"tests/test_report.py": TESTS + PADDED.replace("2026-03-04", "4/3/2026")})
        self.assertNotIn("changed_guards", proof)
        self.assertIsNone(correction.guard_proposal(self.state))

    def test_a_guard_on_a_test_the_change_adds_is_left_alone(self):
        # No original test file defines it, so there is no original test for the criterion to replace.
        plan = [*PLAN[:2], {"id": "AC3", "criterion": "padded", "verification_method": "guard: test_two_digits_stay"}]
        self.approve(plan)
        added = PADDED + (
            "\n    def test_two_digits_stay(self):\n"
            "        self.assertEqual('bob: 2025-12-31\\n', render([('bob', (2025, 12, 31))]))\n"
        )
        proof = self.prove({**CHANGE, "tests/test_report.py": TESTS + added})
        self.assertEqual(["AC3"], [row["id"] for row in proof["changed_guards"]])
        self.assertIsNone(correction.guard_proposal(self.state))

    def test_an_unapproved_plan_a_listed_test_and_a_second_time_are_left_alone(self):
        self.approve(PLAN)
        self.prove()
        self.assertIsNotNone(correction.guard_proposal(self.state))
        events, self.state["user_events"] = self.state["user_events"], []
        self.assertIsNone(correction.guard_proposal(self.state))
        self.state["user_events"] = events
        self.state[correction.KEY] = [{"contract_revision": 1, "tests": [{"criterion": "AC3", **ENTRY}]}]
        self.assertIsNone(correction.guard_proposal(self.state))
        del self.state[correction.KEY]
        sealed = self.state["goal_contract"].pop("task_id")  # a contract that was never sealed approves nothing
        self.assertIsNone(correction.guard_proposal(self.state))
        self.state["goal_contract"]["task_id"] = sealed
        self.assertIsNotNone(correction.guard_proposal(self.state))
        del self.state["settings"]["protected_tests"]
        self.assertIsNone(correction.guard_proposal(self.state))


class OriginalTestTests(unittest.TestCase):
    def original(self, test_id, files):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, text in files.items():
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_text(text)
            return correction._original(test_id, {name: {"sha256": ""} for name in files}, root)

    def test_the_one_original_file_that_defines_the_test_is_named(self):
        go = {
            "report/report_test.go": "package report\nfunc TestRenderMatchesGolden(t *testing.T) {}\n",
            "report/other_test.go": "package report\nfunc TestRenderNothing(t *testing.T) {}\n",
        }
        self.assertEqual(
            {"file": "report/report_test.go", "test": "TestRenderMatchesGolden"},
            self.original("example.com/shop/report::TestRenderMatchesGolden", go),
        )
        self.assertEqual(
            {"file": "tests/test_report.py", "test": GOLDEN},
            self.original(f"tests/test_report.py::ReportTests::{GOLDEN}", {"tests/test_report.py": TESTS}),
        )

    def test_a_test_no_file_or_two_files_define_is_not_named(self):
        self.assertIsNone(self.original("tests.test_report.ReportTests.test_new", {"tests/test_report.py": TESTS}))
        twice = {"report/a_test.go": "func TestRender(t *testing.T) {}\n", "report/b_test.go": "// TestRender again\n"}
        self.assertIsNone(self.original("example.com/shop/report::TestRender", twice))
        link = {"tests/test_report.py": {"symlink": "elsewhere.py"}}
        self.assertIsNone(correction._original(f"tests.test_report.ReportTests.{GOLDEN}", link, Path(".")))


class DispatchTests(Run):
    def revision(self):
        return self.state["regression_proof"]["source_revision"]

    def test_the_review_stage_does_not_launch_and_the_plan_goes_to_plan_review(self):
        self.approve(PLAN)
        self.prove()
        installed = {}

        def install(state, body, **options):
            installed.update(body=body, **options)

        with (
            mock.patch("autocode_goal_lifecycle.install_draft", side_effect=install),
            mock.patch(
                "autocode_planning_cycle.start", side_effect=lambda state: state.update(next_stage="review_plan")
            ),
            mock.patch.object(regression, "before_review"),
        ):
            self.assertTrue(completion_result.before_review(self.state, "tester", self.root, self.run_dir))
        self.assertEqual("review_plan", self.state["next_stage"])
        self.assertEqual(correction.ORIGIN, installed["origin"])
        self.assertFalse(installed["queue_human"])
        self.assertEqual(f"test: {GOLDEN}", installed["body"]["acceptance_criteria"][2]["verification_method"])
        sent = self.state[correction.KEY]
        self.assertEqual([{"criterion": "AC3", **ENTRY}], sent[0]["tests"])
        self.assertEqual(self.state["regression_proof"]["path"], sent[0]["receipt"])
        # No finding was opened for the Builder: the code is right, and the plan is what changes.
        self.assertFalse(self.state.get("findings_ledger"))
        # The same proof is not sent back twice.
        self.assertFalse(correction.relabel_guards(self.state, "tester", self.revision))

    def test_only_a_review_stage_takes_it_and_another_proof_changes_nothing(self):
        self.approve(PLAN)
        self.prove()
        before = copy.deepcopy(self.state)
        self.assertFalse(correction.relabel_guards(self.state, "builder", self.revision))
        # A proof of an earlier source says nothing about this one.
        self.assertFalse(correction.relabel_guards(self.state, "tester", lambda: "another source"))
        self.state["regression_proof"]["unverified"] = ["The suite timed out"]
        self.assertFalse(correction.relabel_guards(self.state, "tester", self.revision))
        before["regression_proof"]["unverified"] = ["The suite timed out"]
        self.assertEqual(before, self.state)


if __name__ == "__main__":
    unittest.main()
