"""Plan approval after the checkout changed under the reviewed plan, through the real CLI (#3080).

A live chat run planned AutoCode in the person's own checkout; a `git pull` landed there seconds
before the chat sent the approval the person had confirmed. The Plan Reviewer's final review no
longer matched the current source, so the core rightly would not approve, but it refused with
"Goal approval requires a current AutoResolver-issued plan request" and left the run where the
status view kept offering the same approval. The approval now keeps the core's own decision (the
plan goes back to the Plan Reviewer) and the person approves the plan that was checked.
"""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

import autocode_taskrun as taskrun

from tests.test_agent_step import fixture_workspace
from tests.test_taskrun import BRIEF, FIXTURE_OPTIONS


@unittest.skipUnless(sys.platform.startswith("linux"), "supervised provider requires supported Linux")
class ApprovalAfterSourceChangeCLITests(unittest.TestCase):
    def setUp(self):
        self.workspace, self.env = fixture_workspace(self)
        # The fixture provider and AutoCode's child processes run `python3` by name.
        first, rest = self.env["PATH"].split(os.pathsep, 1)
        self.env["PATH"] = os.pathsep.join([first, str(Path(sys.executable).parent), rest])

    def cli(self, *args, env=None):
        return subprocess.run(
            [*taskrun.AUTOCODE, "--workspace", str(self.workspace), *args],
            env={**os.environ, **self.env, **(env or {})},
            capture_output=True,
            text=True,
            timeout=240,
        )

    def view(self):
        result = self.cli("--run-dir", str(self.run_dir), "--status")
        self.assertEqual(0, result.returncode, result.stderr)
        return json.loads(result.stdout)["view"]

    def approve(self, token):
        return self.cli(
            "--run-dir", str(self.run_dir), "--approve-goal", "-", env={"AUTOCODE_APPROVE_GOAL_TOKEN": token}
        )

    def commit_upstream_change(self):
        (self.workspace / "upstream.txt").write_text("pulled from upstream\n")
        git = ["git", "-C", str(self.workspace), "-c", "user.name=T", "-c", "user.email=t@example.test"]
        subprocess.run([*git, "add", "upstream.txt"], check=True)
        subprocess.run([*git, "commit", "-qm", "upstream change"], check=True)

    def test_approval_after_the_checkout_moved_rechecks_the_plan_instead_of_refusing(self):
        started = self.cli("--brief", BRIEF, *FIXTURE_OPTIONS, "--in-place")
        self.assertEqual(2, started.returncode, started.stdout + started.stderr)
        self.run_dir = next((self.workspace / ".autocode/runs").iterdir())
        shown = self.view()
        self.assertEqual("approve_plan", shown["needs"]["kind"], shown["needs"])
        reviewed = len(json.loads((self.run_dir / "state.json").read_text())["stages"])

        self.commit_upstream_change()
        approved = self.approve(shown["needs"]["token"])
        self.assertEqual(0, approved.returncode, approved.stdout + approved.stderr)
        self.assertNotIn("AutoResolver-issued plan request", approved.stderr)
        self.assertIn("Not approved yet: the code in the workspace changed after the Plan Reviewer", approved.stdout)
        state = json.loads((self.run_dir / "state.json").read_text())
        self.assertEqual("draft", state["goal_contract"]["approval_status"])
        self.assertEqual(
            {"action": "defer", "reason": "Final planning evidence must match the current source before approval"},
            state["resolver"]["human_disposition"],
        )
        # The view now names the step that re-checks the plan, which a client takes without asking.
        rechecking = self.view()
        self.assertEqual(("RUNNING", "continue"), (rechecking["status"], rechecking["needs"]["kind"]))

        self.assertTrue(rechecking["next_command"].endswith("--no-chat"), rechecking["next_command"])
        continued = self.cli("--run-dir", str(self.run_dir), "--no-chat")
        self.assertEqual(2, continued.returncode, continued.stdout + continued.stderr)
        state = json.loads((self.run_dir / "state.json").read_text())
        self.assertIn("review_plan", [row["stage"] for row in state["stages"][reviewed:]])
        asked = self.view()
        self.assertEqual("approve_plan", asked["needs"]["kind"], asked["needs"])
        self.assertNotEqual(shown["needs"]["token"], asked["needs"]["token"])

        done = self.approve(asked["needs"]["token"])
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        state = json.loads((self.run_dir / "state.json").read_text())
        self.assertEqual("approved", state["goal_contract"]["approval_status"])
        self.assertEqual(asked["needs"]["token"], state["goal_contract"]["approval_event"]["token"])


if __name__ == "__main__":
    unittest.main()
