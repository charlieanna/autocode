"""A change set is checked together before it is done, and each pull request names the others (#2066, step 6).

Drives the real, offline CLI: two linked fixture runs complete through the task-run interface, then
`autocode changeset` names, runs, waives and re-checks the integration command.
"""

import json
import sys
import unittest
from pathlib import Path

import autocode_taskrun as taskrun

from . import test_changeset_cli as base
from .test_changeset_cli import MEMBER_ONLY, register_project

# The consumer's check against the producer's checkout: run its greet.py and compare with what it expects.
CHECK = (
    "import json, os, subprocess, sys; ws = json.loads(os.environ['AUTOCODE_CHANGESET_WORKSPACES']); "
    "out = subprocess.run([sys.executable, os.path.join(ws['project'], 'greet.py'), 'Ada'], "
    "capture_output=True, text=True).stdout.strip(); print(out); sys.exit(0 if out == sys.argv[1] else 1)"
)


class ChangesetDeliveryCliTests(base.ChangesetCliTests):
    def json(self, *args):
        proc = self.changeset(args[0], "--json", *args[1:])  # before any -- command
        return proc.returncode, json.loads(proc.stdout)

    def complete_both(self):
        register_project(self.home, self.member)
        linked = self.link()
        self.assertEqual(0, linked.returncode, linked.stdout + linked.stderr)
        member_dir = Path(json.loads(linked.stdout)["runs"][1]["run_dir"])
        self.member_run = taskrun.TaskRun(
            self.member.resolve(), member_dir, env={**self.env, "AUTOCODE_FIXTURE_IGNORED_STATEMENTS": MEMBER_ONLY}
        )
        self.member_run.env.pop("AUTOCODE_FIXTURE_EXTERNAL_PART")
        self.member_run.advance_until_input()
        self.run.answer(self.run.status()["needs"]["questions"][0]["id"], "This repository only")
        self.run.advance_until_input()
        _, shown = self.json("status", "--run-dir", str(self.run.run_dir))
        code, _ = self.json(
            "approve", "--run-dir", str(self.run.run_dir), "--set-token", shown["approval"]["set_token"]
        )
        self.assertEqual(0, code)
        for run in (self.run, self.member_run):
            self.assertEqual("TASK_COMPLETE", run.advance_until_input()["status"])

    def test_the_set_is_done_only_while_a_passing_check_or_a_waiver_matches_the_code(self):
        self.complete_both()
        here = str(self.run.run_dir)
        _, summary = self.json("status", "--run-dir", here)
        self.assertTrue(summary["verification"]["done"], "no integration command named: each run's own gate")

        code, summary = self.json(
            "check-command", "--run-dir", here, "--in", "greet-history", "--", sys.executable, "-c", CHECK, "Hi, Ada"
        )
        self.assertEqual(0, code, summary)
        verification = summary["verification"]
        self.assertFalse(verification["done"])
        self.assertEqual(["autocode", "changeset", "verify", "--run-dir", here], verification["next_command"])

        code, summary = self.json("verify", "--run-dir", here)
        self.assertEqual(0, code, summary)
        result = summary["verification"]["result"]
        self.assertEqual((False, 1, True), (result["passed"], result["exit_code"], result["current"]))
        self.assertIn("Hello, Ada", result["output"], "the producer's real output is kept")
        self.assertFalse(summary["verification"]["done"])
        body = self.changeset("pr-body", "--run-dir", here, "--target", "greet-history").stdout
        self.assertIn(f"## Change set {summary['changeset']}", body)
        self.assertIn("Integration check not passed yet", body)

        code, refused = self.json("waive", "--run-dir", here, "--reason", " ")
        self.assertEqual(1, code)
        self.assertIn("needs a reason", refused["error"])
        code, summary = self.json("waive", "--run-dir", here, "--reason", "Greeting wording is agreed separately.")
        self.assertEqual(0, code, summary)
        self.assertTrue(summary["verification"]["done"])
        body = self.changeset("pr-body", "--run-dir", here, "--target", "project").stdout
        self.assertIn("waived by the person: Greeting wording is agreed separately.", body)
        self.assertIn("| project (this pull request) | source |", body)

        code, refused = self.json("order", "--run-dir", here, "greet-history")
        self.assertEqual(1, code)
        self.assertIn("name every run", refused["error"])
        code, summary = self.json("order", "--run-dir", here, "project", "greet-history")
        self.assertEqual(["project", "greet-history"], summary["verification"]["merge_order"])

        code, summary = self.json(
            "check-command", "--run-dir", here, "--in", "greet-history", "--", sys.executable, "-c", CHECK, "Hello, Ada"
        )
        self.assertEqual(0, code, summary)
        self.assertFalse(summary["verification"]["done"], "the previous waiver cannot accept a replacement check")
        self.assertIsNone(summary["verification"]["waiver"])
        code, summary = self.json("verify", "--run-dir", here)
        self.assertTrue(summary["verification"]["result"]["passed"], summary["verification"]["result"])
        self.assertTrue(summary["verification"]["done"])
        self.assertIn(
            "Merge order: project, then greet-history.",
            self.changeset("pr-body", "--run-dir", here, "--target", "project").stdout,
        )

        (self.member / "NOTES.md").write_text("changed after the check\n")
        _, summary = self.json("status", "--run-dir", here)
        self.assertFalse(summary["verification"]["result"]["current"], "a later change makes the check stale")
        self.assertFalse(summary["verification"]["done"])
        self.assertIsNone(summary["verification"]["waiver"], "the replaced check's waiver stays withdrawn")
        self.assertIsNotNone(summary["verification"]["next_command"])

    def test_a_failed_check_reopens_the_run_that_diverged(self):
        self.complete_both()
        here = str(self.run.run_dir)
        self.json("check-command", "--run-dir", here, "--in", "greet-history", "--", sys.executable, "-c", CHECK, "Hi")
        self.json("verify", "--run-dir", here)
        code, summary = self.json("reopen", "--run-dir", here, "--target", "project")
        self.assertEqual(0, code, summary)
        [source] = [row for row in summary["runs"] if row["target"] == "project"]
        self.assertNotEqual("TASK_COMPLETE", source["status"], "the follow-up reopened it")
        self.assertIn("waiting_on", summary["verification"])
        self.assertEqual(["project"], summary["verification"]["waiting_on"])

    # The parent's linking tests run once there; here only the delivery tests.
    test_a_linked_run_starts_in_the_registered_project_with_the_ready_brief = None
    test_only_a_registered_project_and_a_found_target_can_be_linked = None
    test_one_set_token_approves_every_shown_plan_and_a_revision_withdraws_it = None
    test_partial_set_approval_blocks_dispatch_and_can_retry_or_release_one_run = None


if __name__ == "__main__":
    unittest.main()
