"""A historical slot hold resumes through its named public action, without a grant."""

import json
import unittest
from datetime import datetime, timedelta

from . import test_recovery_advice_conformance as fixture


class RoutineRecoveryCLITests(unittest.TestCase):
    setUp = fixture.RecoveryAdviceConformanceTests.setUp
    launch = fixture.RecoveryAdviceConformanceTests.launch
    saved = fixture.RecoveryAdviceConformanceTests.saved
    new_run_engine_args = ("--engine", "codex")
    base_checkpoint = fixture.RecoveryAdviceConformanceTests.base_checkpoint
    publish = fixture.RecoveryAdviceConformanceTests.publish

    def historical_hold(self, *, explicit_time_cap=False):
        self.run, stopped = self.base_checkpoint()
        accepted = [
            row
            for row in stopped["stages"]
            if row.get("exit_code") == 0
            and not row.get("rejected")
            and not row.get("abandoned")
            and row.get("finished_at")
            and row.get("schema")
        ]
        self.assertTrue(accepted, "fixture must retain an actual completed provider stage")
        cutoff = max(datetime.fromisoformat(row["finished_at"].replace("Z", "+00:00")) for row in accepted)
        events = []
        for number, offset in enumerate((-4, -3, -2, 1)):
            events.append(
                {
                    "kind": "automatic_timeout_recovery",
                    "actor": "runner",
                    "at": (cutoff + timedelta(seconds=offset)).isoformat(),
                    "attempt_id": f"old-{number}",
                }
            )
        stopped.setdefault("user_events", []).extend(events)
        stopped.update(automatic_recoveries_since_resume=4, consecutive_timeout_recoveries=1)
        stopped["automatic_timeout_recoveries"] = [
            {"stage": "terra", "attempt_id": row["attempt_id"], "timeout_reason": "stage deadline"} for row in events
        ]
        if explicit_time_cap:
            stopped["settings"]["limits"]["max_seconds"] = 1
            stopped["settings"].setdefault("budget_origins", {})["max_seconds"] = "user_explicit"
            stopped["active_seconds"] = 2
        self.publish(stopped, "PAUSED_TIMEOUT_RECOVERY", "Old run-wide recovery slots exhausted")
        self.env.pop("AUTOCODE_FIXTURE_QUOTA_STAGE", None)

    def envelope(self):
        return json.loads(self.launch(["--run-dir", str(self.run), "--status"], 0).stdout)

    def test_named_resume_uses_retained_progress_without_more_slots(self):
        self.historical_hold()
        before = self.envelope()
        # Setting aside the uncertain attempt is the core's own named recovery (continue).
        self.assertEqual("continue", before["view"]["needs"]["kind"])
        self.assertIn("--resume-paused", before["view"]["next_command"])
        self.assertNotIn("--grant-recovery", before["view"]["next_command"])
        self.launch(["--run-dir", str(self.run), "--resume-paused", "--no-chat", "--pause-after-stage"], 2)
        after = self.envelope()
        self.assertEqual("PAUSED_REQUESTED", after["view"]["status"])
        self.assertEqual(before["settings"], after["settings"])
        self.assertEqual(before["view"]["routes"], after["view"]["routes"])
        self.assertGreater(
            len(after["view"]["usage"]["accounting"]["attempts"]),
            len(before["view"]["usage"]["accounting"]["attempts"]),
        )

    def test_rebased_recovery_still_enforces_explicit_active_time_cap(self):
        self.historical_hold(explicit_time_cap=True)
        before = self.envelope()
        self.launch(["--run-dir", str(self.run), "--resume-paused", "--no-chat"], 2)
        after = self.envelope()
        self.assertEqual(before["settings"], after["settings"])
        provider_attempts = lambda view: [
            row for row in view["usage"]["accounting"]["attempts"] if not row.get("runner_owned")
        ]
        self.assertEqual(provider_attempts(before["view"]), provider_attempts(after["view"]))
        self.assertTrue(
            "active-time" in str(after["view"]["stop_reason"]).lower()
            or "time limit" in str(after["view"]["stop_reason"]).lower()
        )
