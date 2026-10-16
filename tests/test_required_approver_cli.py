"""A project's require-approver refuses an approval from any other Git identity, through the real CLI (#722).

Plan and review approval by flag and in the terminal conversation, with the offline fake provider: an approver
whose Git user.email the project's settings do not name is refused before anything is approved, and the run stays
at the same stop; the named approver then approves as before. The run keeps the setting it started with.
"""

import json
import subprocess
import unittest

from . import test_subprocess

REFUSED = "Approval refused: this run takes approvals only from Git user.email"


class RequiredApproverCLITests(unittest.TestCase):
    setUp = test_subprocess.SubprocessFlow.setUp
    launch = test_subprocess.SubprocessFlow.launch
    saved = test_subprocess.SubprocessFlow.saved
    new_run_engine_args = ("--engine", "codex")

    def require(self, text):
        self.env["AUTOCODE_FIXTURE_MODE"] = "human-pending"
        self.config = self.project / ".autocode" / "config.toml"
        self.config.parent.mkdir(exist_ok=True)
        self.config.write_text(text)

    def identify(self, name, email):
        """The approving checkout's Git identity, in the fixture repository's own config."""
        for key, value in (("user.name", name), ("user.email", email)):
            subprocess.run(["git", "-C", str(self.project), "config", key, value], check=True)

    def approvals(self, run):
        view = json.loads(self.launch(["--run-dir", str(run), "--status"], 0).stdout)["view"]
        return [(row["kind"], row["provenance"]["git_user_email"]) for row in view["approvals"]]

    def assert_unchanged(self, before):
        """Nothing was approved: the run is at the same stop, with the same plan, events and request."""
        after = self.saved()[1]
        for key in ("status", "goal_contract", "user_events", "human_reviews", "resolver_human_request"):
            self.assertEqual(before.get(key), after.get(key), key)

    def test_flag_approvals_from_another_git_identity_are_refused_at_the_same_stop(self):
        self.require('require-approver = ["Lead@Example.test", "deputy@example.test"]\n')
        self.identify("Ana Example", "ana@example.test")
        started = self.launch(["Build a greeting tool"], 2)
        self.assertIn("Project settings from", started.stderr)
        run, state = self.saved()
        self.assertEqual(["Lead@Example.test", "deputy@example.test"], state["settings"]["require_approver"])
        args = ["--run-dir", str(run)]
        self.launch([*args, "--answer", "Q1=CLI"], 0)
        self.launch(args, 2)
        before = self.saved()[1]
        self.assertEqual("AWAITING_GOAL_APPROVAL", before["status"])
        plan = before["displayed_goal"]
        refused = self.launch([*args, "--approve-goal", plan, "--as", "Lee Lead"], 2)
        self.assertIn(REFUSED + " Lead@Example.test or deputy@example.test", refused.stderr)
        self.assertIn("this checkout's Git identity is Ana Example <ana@example.test>", refused.stderr)
        self.assertIn("The --as name 'Lee Lead' is a claim and does not count.", refused.stderr)
        self.assert_unchanged(before)
        self.assertEqual("draft", self.saved()[1]["goal_contract"]["approval_status"])
        self.assertEqual([], self.approvals(run))
        # The run keeps the setting it started with: removing the file does not let the approval through.
        self.config.unlink()
        self.assertIn(REFUSED, self.launch([*args, "--approve-goal", plan], 2).stderr)
        self.assert_unchanged(before)
        self.identify("Lee Lead", "lead@example.TEST")  # an address is compared without regard to case
        self.launch([*args, "--approve-goal", plan], 0)
        self.launch(args, 2)
        before = self.saved()[1]
        self.assertEqual("WAITING_FOR_USER", before["status"])
        review = before["displayed_review"]
        self.identify("Ana Example", "ana@example.test")
        refused = self.launch([*args, "--approve-review", "C1", "--review-token", review], 2)
        self.assertIn(REFUSED, refused.stderr)
        self.assert_unchanged(before)
        self.assertNotIn("C1", self.saved()[1].get("human_reviews") or {})
        self.identify("Dee Deputy", "deputy@example.test")
        self.launch([*args, "--approve-review", "C1", "--review-token", review], 0)
        self.launch(args, 0)
        self.assertEqual("TASK_COMPLETE", self.saved()[1]["status"])
        self.assertEqual([("plan", "lead@example.TEST"), ("review", "deputy@example.test")], self.approvals(run))

    def test_terminal_conversation_approvals_from_another_git_identity_are_refused(self):
        self.require('require-approver = "lead@example.test"\n')
        self.identify("Ana Example", "ana@example.test")
        out = self.launch(["Build a greeting tool", "--chat"], 2, answers="CLI\nyes\n")
        self.assertIn(REFUSED + " lead@example.test", out.stdout)
        run, state = self.saved()
        self.assertEqual("AWAITING_GOAL_APPROVAL", state["status"])
        self.assertEqual("draft", state["goal_contract"]["approval_status"])
        self.assertEqual([], self.approvals(run))
        args = ["--run-dir", str(run), "--chat"]
        self.identify("Lee Lead", "lead@example.test")
        self.launch(args, 2, answers="yes\n")  # approved, built, then waiting at the human review
        state = self.saved()[1]
        self.assertEqual("WAITING_FOR_USER", state["status"])
        self.identify("Ana Example", "ana@example.test")
        out = self.launch(args, 2, answers="y\n")
        self.assertIn(REFUSED, out.stdout)
        self.assert_unchanged(state)
        self.identify("Lee Lead", "lead@example.test")
        self.launch(args, 0, answers="y\n")
        self.assertEqual("TASK_COMPLETE", self.saved()[1]["status"])
        self.assertEqual([("plan", "lead@example.test"), ("review", "lead@example.test")], self.approvals(run))


if __name__ == "__main__":
    unittest.main()
