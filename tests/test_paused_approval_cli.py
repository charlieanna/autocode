"""A queued Pause retains a deferred approval until explicit public-CLI resume.

The fixture supplies a final report's retained proposal at its saved boundary.
The real CLI uses the existing supervised fake Codex for the resumed planning
steps, so this regression requires a supported Linux test host.
"""

import hashlib
import os
import subprocess
import unittest
from unittest.mock import patch

import autocode_planning as planning
import autocode_resolver_human as human
import autocode_run_records as records
import autocode_stop as stop
from autocode_taskrun import TaskRun

from . import test_planning as planning_fixtures


class PausedApprovalCliTests(unittest.TestCase):
    # Reuse only the existing isolated joint-planning fixture, never its tests.
    setUp = planning_fixtures.NativeJointFlow.setUp
    launch = planning_fixtures.NativeJointFlow.launch
    saved = planning_fixtures.NativeJointFlow.saved
    prepare = planning_fixtures.NativeJointFlow.prepare
    draft = planning_fixtures.NativeJointFlow.draft
    new_run_engine_args = planning_fixtures.NativeJointFlow.new_run_engine_args

    @staticmethod
    def provider_artifacts(run_dir):
        return {
            str(path.relative_to(run_dir)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (run_dir / "iterations").rglob("*.jsonl")
        }

    def test_deferred_approval_pause_holds_without_launch_until_explicit_resume(self):
        run_dir, state = self.draft()
        run = TaskRun(self.project, run_dir, command=self.entry, env=self.env, timeout=240, cwd=self.root)
        before = self.provider_artifacts(run_dir)

        # Start an unfinished review cycle from the saved draft. Retaining the
        # completed cycle while merely clearing its token would leave an
        # inconsistent final-report fixture, rather than work still to review.
        planning.start(state)
        # AutoResolver genuinely defers this proposal until independent review
        # finishes; do not mock that adjudication.
        human.queue(
            state,
            "goal_approval",
            {"stage": "astra_finalize"},
            status="AWAITING_GOAL_APPROVAL",
            next_stage="astra_challenge",
        )
        submitted = subprocess.run(
            [
                *self.entry,
                "intervention",
                "submit",
                "--workspace",
                str(self.project),
                "--run-dir",
                str(run_dir),
                "--request-id",
                "pause-after-review",
                "--kind",
                "pause",
                "--json",
            ],
            env=self.env,
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(0, submitted.returncode, submitted.stdout + submitted.stderr)
        # This is the same commit boundary as a final report consuming the
        # already accepted request. The public client drives everything after it.
        with patch.dict(os.environ, self.env):
            self.assertTrue(stop.consume(state, run_dir, self.project, write_json=records.write_json, now=records.now))

        paused = run.advance()
        self.assertEqual("PAUSED_INTERVENTION", paused["status"])
        self.assertEqual("resume", paused["needs"]["kind"])
        self.assertIn("--resume-paused", paused["next_command"])
        self.assertEqual(before, self.provider_artifacts(run_dir), "Pause must launch no further provider")

        resumed = run.resume_paused()
        self.assertNotEqual(
            before,
            self.provider_artifacts(run_dir),
            "Explicit resume must advance planning: "
            + repr({key: resumed.get(key) for key in ("status", "needs", "next_stage", "stop_reason")}),
        )
        self.assertEqual("approve_plan", resumed["needs"]["kind"], resumed.get("stop_reason"))
        self.assertFalse(resumed["done"])
        self.assertFalse((self.project / "greet.py").exists(), "Resume cannot approve the plan or build")


if __name__ == "__main__":
    unittest.main()
