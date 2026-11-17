"""Resume a legacy planning-only hold through the public CLI."""

import json
import shlex
import unittest

import autocode_route_fit as route_fit

from . import test_subprocess
from .operational_fixtures import legacy_publication


class PlanningRecoveryCLITests(unittest.TestCase):
    setUp = test_subprocess.SubprocessFlow.setUp
    launch = test_subprocess.SubprocessFlow.launch
    saved = test_subprocess.SubprocessFlow.saved
    new_run_engine_args = ("--engine", "codex")

    def legacy_hold(self):
        self.env["AUTOCODE_FIXTURE_MODE"] = "no-human"
        self.launch(["Build greeting", "--chat"], 2, answers="CLI\nno\n")
        run, state = self.saved()
        self.launch(["--run-dir", str(run), "--approve-goal", state["displayed_goal"]], 0)
        _, state = self.saved()
        self.assertFalse(state.get("active_stage"))
        self.assertFalse(any(row.get("stage") in ("orchestrator", "builder") for row in state["stages"]))
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
        state["stop_reason"] = "Repeated unchanged implementation batches require review"
        legacy_publication(state, run, pause="PAUSED_NO_PROGRESS")
        (run / "state.json").write_text(json.dumps(state))
        return run

    def test_resume_reaches_builder_without_granting_more_timeout_recoveries(self):
        run = self.legacy_hold()
        before = json.loads(self.launch(["--run-dir", str(run), "--status"], 0).stdout)
        self.assertEqual({"kind": "continue", "action": "--resume-paused"}, before["view"]["needs"])
        self.assertIsNone(before["view"]["chat"]["input"])
        self.launch([*shlex.split(before["view"]["next_command"])[1:], "--pause-after-stage"], 2)
        view = json.loads(self.launch(["--run-dir", str(run), "--status"], 0).stdout)["view"]
        self.assertEqual("PAUSED_REQUESTED", view["status"])
        self.assertEqual("builder", view["next_stage"])
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
        self.assertNotEqual(0, result.returncode)
        # The hold is one AutoCode continues itself (the status view names --resume-paused), so the refusal
        # names why the retry does not apply and claims no blocked recovery (#3743 aligned the two).
        self.assertIn("--retry-failed-stage requires a run paused for repeated failure", result.stderr)
        self.assertNotIn("could not safely continue", result.stderr)
        self.assertFalse((self.project / "greet.py").exists())
        self.assertFalse(
            any(
                event.get("kind") == "recovery_accounting_correction"
                for event in self.saved()[1].get("user_events", [])
            )
        )
