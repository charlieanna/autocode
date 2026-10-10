"""A deferred plan approval restarts planning a bounded number of times.

docs/bugs/2026-09-30-unbounded-planning-restart.md: each deferral restarted planning with a fresh
review allowance, so a deferral that kept recurring spent review calls without limit.
"""

import tempfile
import unittest
from pathlib import Path

import autocode_resolver_human as human
import autocode_run_records as records
import autocode_util as util


def joint_run(run_dir):
    """A joint-planning run whose draft has no questions but no final Plan Reviewer token: every
    goal-approval proposal is deferred with 'Independent planning must finish before requesting approval'."""
    contract = {"task_id": "t1", "revision": 1, "body": {"open_blocking_questions": []}}
    contract.update(hash=util.digest(contract), approval_status="draft")
    return {
        "task": "Build the greeting CLI",
        "task_id": "t1",
        "workspace": str(run_dir),
        "settings": {"joint_planning": True},
        "status": "RUNNING",
        "phase": "PLANNING",
        "run_dir": str(run_dir),
        "user_events": [],
        "goal_contract": contract,
        "planning": {"astra_calls": 2, "reports": {}, "final_token": None},
    }


def ask_for_approval(state, run_dir):
    """What a final review does, then the writer boundary that adjudicates it."""
    human.queue(
        state, "goal_approval", {"stage": "astra_finalize"}, status="AWAITING_GOAL_APPROVAL", next_stage="astra_plan"
    )
    records.normalize_human_boundary(state, run_dir)


class DeferredApprovalRestarts(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.run_dir = Path(directory.name)
        self.state = joint_run(self.run_dir)

    def test_planning_restarts_twice_then_pauses_instead_of_looping(self):
        for _ in range(2):
            ask_for_approval(self.state, self.run_dir)
            self.assertEqual(
                ("RUNNING", "PLANNING", "astra_challenge"),
                (self.state["status"], self.state["phase"], self.state["next_stage"]),
            )
        ask_for_approval(self.state, self.run_dir)
        self.assertEqual("PAUSED_APPROVAL_DEFERRED", self.state["status"])
        self.assertIn("Independent planning must finish", self.state["stop_reason"])
        # A resume runs exactly one more planning cycle; it never heads for execution unapproved.
        self.assertEqual("astra_challenge", self.state["next_stage"])
        self.assertIsNone(self.state.get(human.PRIVATE))
        self.assertIsNone(human.current(self.state))

    def test_a_resume_allows_one_cycle_and_the_same_deferral_pauses_again(self):
        for _ in range(3):
            ask_for_approval(self.state, self.run_dir)
        self.state.update(status="RUNNING", phase="PLANNING")  # what --resume-paused leaves
        ask_for_approval(self.state, self.run_dir)
        self.assertEqual("PAUSED_APPROVAL_DEFERRED", self.state["status"])

    def test_new_user_input_renews_the_allowance(self):
        for _ in range(2):
            ask_for_approval(self.state, self.run_dir)
        self.state["user_events"].append({"kind": "brief_feedback", "id": "feedback-1", "actor": "user_cli"})
        ask_for_approval(self.state, self.run_dir)
        self.assertEqual(("RUNNING", "astra_challenge"), (self.state["status"], self.state["next_stage"]))

    def test_feedback_queued_while_the_run_worked_renews_the_allowance(self):
        for _ in range(2):
            ask_for_approval(self.state, self.run_dir)
        self.state["user_events"].append(
            {"kind": "brief_feedback", "id": "intervention-1", "actor": "user_intervention"}
        )
        ask_for_approval(self.state, self.run_dir)
        self.assertEqual(("RUNNING", "astra_challenge"), (self.state["status"], self.state["next_stage"]))

    def test_runner_bookkeeping_does_not_renew_the_allowance(self):
        # Recovery receipts, reroutes and other runner events land in user_events too; the
        # pause message says "since your last input", so only user-authored events may renew.
        for _ in range(2):
            ask_for_approval(self.state, self.run_dir)
        self.state["user_events"].append({"kind": "automatic_timeout_recovery", "actor": "runner"})
        ask_for_approval(self.state, self.run_dir)
        self.assertEqual("PAUSED_APPROVAL_DEFERRED", self.state["status"])


if __name__ == "__main__":
    unittest.main()
