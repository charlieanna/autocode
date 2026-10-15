"""Who approved each plan and review, through the real CLI with the offline fake provider (#722).

Plan approval by flag and in the terminal conversation, artifact review, completion: every approval
shows its provenance in `--status` and the completion summary, and no token reaches it.
"""

import json
import socket
import subprocess
import unittest
from pathlib import Path

from . import test_subprocess

IDENTITY = {"git_user_name": "Ana Example", "git_user_email": "ana@example.test"}


class ApprovalProvenanceCLITests(unittest.TestCase):
    setUp = test_subprocess.SubprocessFlow.setUp
    launch = test_subprocess.SubprocessFlow.launch
    saved = test_subprocess.SubprocessFlow.saved
    new_run_engine_args = ("--engine", "codex")

    def identify(self):
        self.env["AUTOCODE_FIXTURE_MODE"] = "human-pending"
        for key, value in (("user.name", "Ana Example"), ("user.email", "ana@example.test")):
            subprocess.run(["git", "-C", str(self.project), "config", key, value], check=True)

    def status(self, run):
        return json.loads(self.launch(["--run-dir", str(run), "--status"], 0).stdout)["view"]

    def test_flag_approvals_record_who_approved_without_their_tokens(self):
        self.identify()
        self.launch(["Build a greeting tool"], 2)
        run, _ = self.saved()
        args = ["--run-dir", str(run)]
        self.launch([*args, "--answer", "Q1=CLI"], 0)
        self.launch(args, 2)
        plan = self.saved()[1]["displayed_goal"]
        self.assertEqual([], self.status(run)["approvals"])
        self.env["AUTOCODE_APPROVE_GOAL_TOKEN"] = plan
        self.launch([*args, "--approve-goal", "-", "--as", "Bo at the shared desk"], 0)
        del self.env["AUTOCODE_APPROVE_GOAL_TOKEN"]
        self.launch(args, 2)
        review = self.saved()[1]["displayed_review"]
        self.launch([*args, "--approve-review", "C1", "--review-token", review], 0)
        completed = self.launch(args, 0)
        view = self.status(run)
        self.assertTrue(view["done"], view["status"])
        host = socket.gethostname() or None
        self.assertEqual(
            [
                {
                    "kind": "plan",
                    "at": view["approvals"][0]["at"],
                    "actor": "user_cli",
                    "contract_token": plan,
                    "criterion": None,
                    "provenance": {
                        **IDENTITY,
                        "host": host,
                        "command": "autocode --workspace --run-dir --approve-goal --as",
                        "claimed_as": "Bo at the shared desk",
                    },
                },
                {
                    "kind": "review",
                    "at": view["approvals"][1]["at"],
                    "actor": "user_cli",
                    "contract_token": None,
                    "criterion": "C1",
                    "provenance": {
                        **IDENTITY,
                        "host": host,
                        "command": "autocode --workspace --run-dir --approve-review --review-token",
                        "claimed_as": None,
                    },
                },
            ],
            view["approvals"],
        )
        self.assertNotIn(review, json.dumps(view["approvals"]))
        self.assertEqual(view["approved_contract"]["token"], plan)
        summary = completed.stdout
        self.assertIn("Approvals:", summary)
        self.assertIn(f"Plan {plan} approved", summary)
        self.assertIn(f"by Ana Example <ana@example.test> on {host}", summary)
        self.assertIn("who gave the name 'Bo at the shared desk' (a claim, not verified)", summary)
        self.assertIn("Review of C1 approved", summary)
        self.assertLess(summary.index("Approvals:"), summary.index("Cost:"))

    def test_terminal_conversation_approvals_record_who_approved(self):
        self.identify()
        self.launch(["Build a greeting tool", "--chat"], 0, answers="CLI\nyes\ny\n")
        run, state = self.saved()
        self.assertEqual("TASK_COMPLETE", state["status"])
        approvals = self.status(run)["approvals"]
        self.assertEqual(["plan", "review"], [row["kind"] for row in approvals])
        for row in approvals:
            self.assertEqual(
                {
                    **IDENTITY,
                    "host": socket.gethostname() or None,
                    "command": "autocode --workspace --engine --chat --in-place",
                    "claimed_as": None,
                },
                row["provenance"],
            )
        # The approval the contract and the review binding hold is the stamped event itself.
        self.assertEqual(approvals[0]["provenance"], state["goal_contract"]["approval_event"]["provenance"])
        self.assertEqual(approvals[1]["provenance"], state["human_reviews"]["C1"]["provenance"])
        self.assertNotIn(str(Path(self.project)), json.dumps(approvals))


if __name__ == "__main__":
    unittest.main()
