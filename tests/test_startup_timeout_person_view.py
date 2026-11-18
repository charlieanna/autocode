"""An exhausted provider startup is a recorded end, not a person decision.

Since #3798 a spent automatic recovery allowance is AutoCode's to close: a checkpoint saved at that
stop shows ``continue`` and the next resume ends the run with a recorded outcome
(``autocode_recovery_exhaustion``). The outcome still says the model step never started.
"""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode as runner
import autocode_operational_disposition as disposition
import autocode_recovery_exhaustion as exhaustion
import autocode_resolver_human as human
import autocode_run_view as run_view
import autocode_stop as stop
import autocode_support as support

from . import test_resolver_human as fixture
from .operational_fixtures import legacy_publication


class StartupTimeoutPersonViewTests(unittest.TestCase):
    setUp = fixture.ResolverHumanTests.setUp
    contract = fixture.ResolverHumanTests.contract

    def exhausted(self, *, stall="before_first_event", pause="PAUSED_TIMEOUT_RECOVERY", legacy=False):
        self.state.update(
            sessions={},
            status=pause,
            next_stage="review_plan",
            automatic_recoveries_since_resume=3,
            consecutive_timeout_recoveries=3,
            automatic_timeout_recoveries=[
                {"stage": "review_plan", "timeout_kind": "idle", "stall_phase": stall, "completed_tool_calls": 0}
                for _ in range(3)
            ],
        )
        run = Path(self.state["run_dir"])
        error = support.Paused(pause, "Provider startup did not respond after bounded attempts")
        if legacy:
            legacy_publication(self.state, run, pause=pause)
        else:
            self.assertTrue(runner.resolver_runtime.record_operational_exhaustion(runner, self.state, run, error))
        with contextlib.redirect_stderr(io.StringIO()):
            if legacy:
                # Retain the authentic old publisher's bytes. Today's writer
                # intentionally withdraws this obsolete machinery publication.
                support.atomic_json(run / "state.json", self.state)
            else:
                runner.write_json(run / "state.json", self.state)
        saved = support.read(run / "state.json")
        return saved, run

    def assert_nothing_asked(self, view):
        # A resume AutoCode settles itself (autocode_recovery_exhaustion), never a machinery question.
        self.assertEqual({"kind": "continue", "action": "--resume-paused"}, view["needs"])
        self.assertTrue(view["next_command"].endswith(" --resume-paused"), view["next_command"])
        self.assertIsNone(view["chat"]["input"])
        self.assertEqual(exhaustion.NOTICE, view["stop_reason"])
        text = " ".join(row["text"] for row in view["chat"]["messages"])
        self.assertNotIn("Provide corrective information", text)
        self.assertNotIn("grant-recovery", text + str(view["next_command"]))
        self.assertFalse(view["done"])

    def assert_ended(self, saved, run):
        self.assertTrue(exhaustion.settle(saved, at="2026-11-18T00:00:00Z"))
        view = run_view.view(saved, run_dir=run, workspace=Path(saved["workspace"]))
        self.assertIsNone(view["needs"])
        self.assertIsNone(view["next_command"])
        self.assertEqual("stopped", view["recovery"]["category"])
        self.assertEqual("could_not_finish", stop.outcome(saved)["result"])
        return view

    def test_exhausted_startup_asks_nothing_and_its_end_names_the_model_step(self):
        saved, run = self.exhausted()
        self.assert_nothing_asked(run_view.view(saved, run_dir=run, workspace=Path(saved["workspace"])))
        view = self.assert_ended(saved, run)
        self.assertIn("model step did not start", view["recovery"]["what_happened"])
        self.assertNotIn("the cause is unknown", view["recovery"]["what_happened"])
        self.assertNotIn("source binding", view["recovery"]["what_happened"])

    def test_public_cli_status_reports_the_end_without_a_person_question(self):
        saved, run = self.exhausted()
        self.assertTrue(exhaustion.settle(saved, at="2026-11-18T00:00:00Z"))
        runner.write_json(run / "state.json", saved)
        with tempfile.TemporaryDirectory() as home:
            result = subprocess.run(
                [sys.executable, runner.__file__, "--status", "--workspace", saved["workspace"], "--run-dir", str(run)],
                capture_output=True,
                text=True,
                check=True,
                env={**os.environ, "AUTOCODE_HOME": home},
            )
        view = json.loads(result.stdout)["view"]
        self.assertEqual("stopped", view["summary"]["state"])
        self.assertIn("model step", view["summary"]["sentence"].lower())
        self.assertIsNone(view["summary"]["next_command"])
        self.assertIsNone(view["chat"]["input"])
        self.assertNotIn("Pending questions", result.stderr)
        with tempfile.TemporaryDirectory() as home:
            explanation = subprocess.run(
                [
                    sys.executable,
                    runner.__file__,
                    "--explain",
                    "--workspace",
                    saved["workspace"],
                    "--run-dir",
                    str(run),
                ],
                capture_output=True,
                text=True,
                check=True,
                env={**os.environ, "AUTOCODE_HOME": home},
            )
        self.assertIn("model step", explanation.stdout)
        self.assertNotIn("grant-recovery", explanation.stdout)

    def test_reconciliation_does_not_offer_a_retry_for_this_stop(self):
        saved, run = self.exhausted()
        with patch("autocode_checkpoint_reconciliation.offer", return_value={"kind": "resume"}) as offer:
            view = run_view.view(saved, run_dir=run, workspace=Path(saved["workspace"]))
        offer.assert_not_called()
        self.assert_nothing_asked(view)

    def test_a_stale_but_authenticated_request_is_withdrawn_without_asking(self):
        saved, run = self.exhausted(legacy=True)
        saved["active_seconds"] += 1
        self.assertIsNone(human.current(saved))
        self.assertIsNotNone(human.retained(saved))
        self.assert_nothing_asked(run_view.view(saved, run_dir=run, workspace=Path(saved["workspace"])))
        self.assert_ended(saved, run)
        self.assertIsNone(human.retained(saved))

    def test_a_tampered_receipt_does_not_claim_a_classified_failure(self):
        import autocode_startup_timeout_view as startup_view

        saved, run = self.exhausted()
        self.assertIsNotNone(startup_view.presentation(saved))
        original = disposition.held(saved)["receipt"]["id"]
        saved["resolver"]["operational_receipts"][original]["receipt"]["evidence"]["status"] = "tampered"
        self.assertIsNone(startup_view.presentation(saved))
        self.assert_nothing_asked(run_view.view(saved))
        self.assert_ended(saved, run)

    def test_altered_timeout_history_cannot_be_classified_by_an_old_receipt(self):
        import autocode_startup_timeout_view as startup_view

        saved, run = self.exhausted(stall="during_reasoning_or_output", legacy=True)
        for row in saved["automatic_timeout_recoveries"]:
            row["stall_phase"] = "before_first_event"
        self.assertIsNotNone(human.retained(saved), "the old receipt remains readable")
        self.assertIsNone(startup_view.presentation(saved), "unbound state cannot classify the old stop")
        self.assert_nothing_asked(run_view.view(saved))

    def test_a_stall_after_the_first_event_is_not_called_a_startup_failure(self):
        saved, run = self.exhausted(stall="during_reasoning_or_output")
        self.assert_nothing_asked(run_view.view(saved, run_dir=run, workspace=Path(saved["workspace"])))
        view = self.assert_ended(saved, run)
        self.assertNotIn("did not start", view["recovery"]["what_happened"])

    def test_an_explicit_time_limit_keeps_its_existing_action(self):
        # This fixture retains its unknown saved bound origin.
        saved, run = self.exhausted(pause="PAUSED_TIME_LIMIT")
        view = run_view.view(saved, run_dir=run, workspace=Path(saved["workspace"]))
        self.assertEqual("answer", view["needs"]["kind"])
        self.assertIsNotNone(view["next_command"])
        self.assertIsNone(exhaustion.pending(saved))


if __name__ == "__main__":
    unittest.main()
