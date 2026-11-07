"""Resume a legacy planning-only hold through the public CLI."""

import json
import unittest

import autocode as runner
import autocode_route_fit as route_fit
import autocode_support as support

from . import test_planning, test_subprocess


class PlanningRecoveryCLITests(unittest.TestCase):
    setUp = test_subprocess.SubprocessFlow.setUp
    launch = test_subprocess.SubprocessFlow.launch
    saved = test_subprocess.SubprocessFlow.saved
    prepare = test_planning.NativeJointFlow.prepare
    draft = test_planning.JointFlow.draft
    new_run_engine_args = test_planning.NativeJointFlow.new_run_engine_args

    def legacy_hold(self):
        run, state = self.draft()
        self.launch(["--run-dir", str(run), "--approve-goal", state["displayed_goal"]], 0)
        _, state = self.saved()
        # A saved checkpoint from before the accounting fix: planning used
        # recovery credits but no Builder has ever been dispatched.
        state.update(no_progress_batches=3, automatic_recoveries_since_resume=2, consecutive_timeout_recoveries=0)
        state["automatic_timeout_recoveries"] = [
            {
                "stage": "requirements_gather",
                "attempt_id": f"old-planning-{i}",
                "changed_files": [],
                "timeout_reason": "stage limit",
            }
            for i in range(3)
        ]
        runner.resolver_runtime.record_operational_exhaustion(
            runner,
            state,
            run,
            support.Paused("PAUSED_NO_PROGRESS", "Repeated unchanged implementation batches require review"),
        )
        runner.write_json(run / "state.json", state)
        return run

    def test_resume_reaches_builder_without_granting_more_timeout_recoveries(self):
        run = self.legacy_hold()
        before = json.loads(self.launch(["--run-dir", str(run), "--status"], 0).stdout)
        self.launch(["--run-dir", str(run), "--resume-paused", "--no-chat", "--pause-after-stage"], 2)
        view = json.loads(self.launch(["--run-dir", str(run), "--status"], 0).stdout)["view"]
        self.assertEqual("terra", view["next_stage"])
        self.assertEqual("PAUSED_REQUESTED", view["status"])
        self.launch(["--run-dir", str(run), "--resume-paused", "--no-chat", "--pause-after-stage"], 2)
        self.assertTrue((self.project / "greet.py").is_file())
        after = json.loads(self.launch(["--run-dir", str(run), "--status"], 0).stdout)
        self.assertEqual("PAUSED_REQUESTED", after["view"]["status"])
        self.assertEqual(route_fit.identity(before["settings"]), route_fit.identity(after["settings"]))
        self.assertEqual(before["view"]["routes"], after["view"]["routes"])
        attempts = lambda view: {row["identity"] for row in view["usage"]["accounting"]["attempts"]}
        self.assertLess(attempts(before["view"]), attempts(after["view"]))

    def test_invalid_retry_does_not_apply_accounting_correction(self):
        run = self.legacy_hold()
        result = self.launch(["--run-dir", str(run), "--resume-paused", "--retry-failed-stage", "--no-chat"], 2)
        self.assertIn("Input rejected:", result.stderr)
        self.assertFalse((self.project / "greet.py").exists())
        self.assertFalse(
            any(
                event.get("kind") == "recovery_accounting_correction"
                for event in self.saved()[1].get("user_events", [])
            )
        )
