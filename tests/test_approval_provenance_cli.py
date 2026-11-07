"""Who approved each plan and review, edited the plan and resumed a pause, through the real CLI (#722).

Plan approval by flag and in the terminal conversation, a goal edit, an explicit resume, artifact
review, completion, all with the offline fake provider: each shows its provenance in `--status` and
the completion summary, and no token reaches it.
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
                    "pause_status": None,
                    "source": None,
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
                    "pause_status": None,
                    "source": None,
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
        # The evidence report, which the pull request body wraps exactly, lists the same records.
        report = json.loads(self.launch([*args, "--status", "--inspect-evidence"], 0).stdout)["view"]["evidence_report"]
        self.assertEqual("current", report["availability"], report["reasons"])
        self.assertEqual(view["approvals"], report["document"]["approvals"])
        self.assertEqual(report["markdown"], (run / "evidence.md").read_text())
        self.assertIn(
            f"| Plan {plan} approved | Ana Example &lt;ana@example.test&gt; | {host or 'not recorded'} "
            "| autocode --workspace --run-dir --approve-goal --as | Bo at the shared desk (a claim, not verified) |",
            report["markdown"],
        )
        self.assertIn(
            f"| Review of C1 approved | Ana Example &lt;ana@example.test&gt; | {host or 'not recorded'} "
            "| autocode --workspace --run-dir --approve-review --review-token | none given |",
            report["markdown"],
        )
        self.assertNotIn(review, report["markdown"] + json.dumps(report["document"]))

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

    def test_an_unreadable_edited_goal_is_refused_without_pausing_the_run(self):
        """A mistyped --edit-goal path paused the run as PAUSED_INVALID_OUTPUT (#2389); it is input to reject."""
        self.identify()
        self.launch(["Build a greeting tool"], 2)
        run, _ = self.saved()
        args = ["--run-dir", str(run)]
        self.launch([*args, "--answer", "Q1=CLI"], 0)
        self.launch(args, 2)
        before = self.saved()[1]
        self.assertEqual("AWAITING_GOAL_APPROVAL", before["status"])
        broken = self.root / "broken.json"
        broken.write_text("{not json")
        for path in (self.root / "missing.json", broken):
            with self.subTest(path=path.name):
                result = self.launch([*args, "--edit-goal", str(path)], 2)
                self.assertIn("Input rejected: Cannot read the revised goal", result.stderr)
                self.assertIn(path.name, result.stderr)
                after = self.saved()[1]
                self.assertEqual("AWAITING_GOAL_APPROVAL", after["status"])
                self.assertEqual(before["goal_contract"], after["goal_contract"])

    def test_a_goal_edit_and_a_resume_record_who_made_them(self):
        self.identify()
        self.launch(["Build a greeting tool"], 2)
        run, _ = self.saved()
        args = ["--run-dir", str(run)]
        self.launch([*args, "--answer", "Q1=CLI"], 0)
        self.launch(args, 2)
        edited = self.saved()[1]["goal_contract"]["body"]
        edited["constraints"].append("Keep names Unicode")
        path = self.root / "edited.json"
        path.write_text(json.dumps(edited))
        self.launch([*args, "--edit-goal", str(path), "--as", "Bo at the shared desk"], 0)
        contract = self.saved()[1]["goal_contract"]
        draft = f"r{contract['revision']}:{contract['hash']}"
        self.launch(args, 2)
        plan = self.saved()[1]["displayed_goal"]
        self.assertEqual(draft, plan)
        self.launch([*args, "--approve-goal", plan], 0)
        self.launch([*args, "--pause-after-stage"], 2)
        self.assertEqual("PAUSED_REQUESTED", self.saved()[1]["status"])
        self.assertEqual(["edit", "plan"], [row["kind"] for row in self.status(run)["approvals"]])
        self.launch([*args, "resume", "--as", "Cy"], 2)  # resumed, then stopped at the human review
        review = self.saved()[1]["displayed_review"]
        self.launch([*args, "--approve-review", "C1", "--review-token", review], 0)
        completed = self.launch(args, 0)
        view = self.status(run)
        self.assertTrue(view["done"], view["status"])
        host = socket.gethostname() or None
        self.assertEqual(
            [
                ("edit", draft, None, "autocode --workspace --run-dir --edit-goal --as", "Bo at the shared desk"),
                ("plan", plan, None, "autocode --workspace --run-dir --approve-goal", None),
                ("resume", None, "PAUSED_REQUESTED", "autocode resume --workspace --run-dir --as", "Cy"),
                ("review", None, None, "autocode --workspace --run-dir --approve-review --review-token", None),
            ],
            [
                (
                    row["kind"],
                    row["contract_token"],
                    row["pause_status"],
                    row["provenance"]["command"],
                    row["provenance"]["claimed_as"],
                )
                for row in view["approvals"]
            ],
        )
        for row in view["approvals"]:
            self.assertEqual("user_cli", row["actor"])
            self.assertEqual({**IDENTITY, "host": host}, {key: row["provenance"][key] for key in (*IDENTITY, "host")})
        self.assertEqual(contract["created_at"], view["approvals"][0]["at"])
        self.assertNotIn(review, json.dumps(view["approvals"]))
        summary = completed.stdout
        self.assertIn(f"Edited plan {draft} saved", summary)
        self.assertIn("who gave the name 'Bo at the shared desk' (a claim, not verified)", summary)
        self.assertIn("Resumed from PAUSED_REQUESTED", summary)
        self.assertIn("via `autocode resume --workspace --run-dir --as`, who gave the name 'Cy'", summary)
        self.assertLess(summary.index("Approvals:"), summary.index("Cost:"))

    def test_a_resume_a_kept_pause_request_holds_records_nothing(self):
        self.identify()
        self.launch(["Build a greeting tool"], 2)
        run, _ = self.saved()
        args = ["--run-dir", str(run)]
        self.launch([*args, "--answer", "Q1=CLI"], 0)
        self.launch(args, 2)
        self.launch([*args, "--approve-goal", self.saved()[1]["displayed_goal"]], 0)
        (run / "pause-requested").write_text("operator")
        self.launch(args, 2)
        held = self.saved()[1]
        self.assertEqual("PAUSED_REQUESTED", held["status"])
        for _ in range(2):  # the file keeps the run at its pause: no stage launches and no resume is recorded
            out = self.launch([*args, "resume", "--as", "Cy"], 2)
            self.assertIn("PAUSED_REQUESTED: Pause requested", out.stdout + out.stderr)
            state = self.saved()[1]
            self.assertEqual(("PAUSED_REQUESTED", len(held["stages"])), (state["status"], len(state["stages"])))
            self.assertIsNone(state.get("pause_acknowledgements"))
            self.assertEqual(["plan"], [row["kind"] for row in self.status(run)["approvals"]])
        (run / "pause-requested").unlink()
        self.launch([*args, "resume", "--as", "Cy"], 2)  # released, then stopped at the human review
        self.assertGreater(len(self.saved()[1]["stages"]), len(held["stages"]))
        self.assertEqual(
            [("plan", None, None), ("resume", "PAUSED_REQUESTED", "Cy")],
            [
                (row["kind"], row["pause_status"], (row["provenance"] or {}).get("claimed_as"))
                for row in self.status(run)["approvals"]
            ],
        )

    def test_a_resume_is_recorded_only_once_it_releases_the_operational_pause(self):
        self.identify()
        self.env["AUTOCODE_FIXTURE_MODE"] = "rework"
        self.launch(["Build a greeting tool", "--chat", "--max-iterations", "1"], 2, answers="CLI\nyes\n")
        run, state = self.saved()
        from tools.goal_fixtures import assert_operational_wait

        assert_operational_wait(self, state, "PAUSED_ITERATION_LIMIT")
        args = ["--run-dir", str(run)]
        self.launch([*args, "--resume-paused"], 2)  # the iteration limit still holds the run
        self.assertEqual(["plan"], [row["kind"] for row in self.status(run)["approvals"]])
        self.launch([*args, "--resume-paused", "--max-iterations", "3"], 0)
        self.assertEqual("TASK_COMPLETE", self.saved()[1]["status"])
        resumes = [row for row in self.status(run)["approvals"] if row["kind"] == "resume"]
        self.assertEqual(
            [("PAUSED_ITERATION_LIMIT", "autocode --workspace --run-dir --resume-paused --max-iterations", None)],
            [(row["pause_status"], row["provenance"]["command"], row["provenance"]["claimed_as"]) for row in resumes],
        )
        self.assertEqual(IDENTITY, {key: resumes[0]["provenance"][key] for key in IDENTITY})


if __name__ == "__main__":
    unittest.main()
