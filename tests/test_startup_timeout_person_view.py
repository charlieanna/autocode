"""An exhausted provider startup is a reported failure, not a person decision."""

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
import autocode_resolver_human as human
import autocode_run_view as run_view
import autocode_support as support

from . import test_resolver_human as fixture


class StartupTimeoutPersonViewTests(unittest.TestCase):
    setUp = fixture.ResolverHumanTests.setUp
    contract = fixture.ResolverHumanTests.contract

    def exhausted(self, *, stall="before_first_event", pause="PAUSED_TIMEOUT_RECOVERY"):
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
        self.assertTrue(runner.resolver_runtime.record_operational_exhaustion(runner, self.state, run, error))
        with contextlib.redirect_stderr(io.StringIO()):
            runner.write_json(run / "state.json", self.state)
        saved = support.read(run / "state.json")
        self.assertIsNotNone(human.current(saved))
        return saved, run

    def test_exhausted_startup_reports_blocked_without_a_machinery_question(self):
        saved, run = self.exhausted()
        view = run_view.view(saved, run_dir=run, workspace=Path(saved["workspace"]))
        self.assertIsNone(view["needs"])
        self.assertIsNone(view["next_command"])
        self.assertEqual("blocked", view["recovery"]["category"])
        self.assertIn("model step", view["recovery"]["what_happened"].lower())
        self.assertIsNone(view["chat"]["input"])
        self.assertIn("failed attempts", view["explanation"]["what_it_means"])
        self.assertNotIn("source binding", view["explanation"]["what_it_means"])
        text = " ".join(row["text"] for row in view["chat"]["messages"])
        self.assertNotIn("Provide corrective information", text)
        self.assertNotIn("grant-recovery", text)
        self.assertFalse(view["done"])

    def test_public_cli_status_reports_the_block_without_a_person_question(self):
        saved, run = self.exhausted()
        with tempfile.TemporaryDirectory() as home:
            result = subprocess.run(
                [sys.executable, runner.__file__, "--status", "--workspace", saved["workspace"], "--run-dir", str(run)],
                capture_output=True,
                text=True,
                check=True,
                env={**os.environ, "AUTOCODE_HOME": home},
            )
        view = json.loads(result.stdout)["view"]
        self.assertEqual("blocked", view["summary"]["state"])
        self.assertIn("model step", view["summary"]["sentence"].lower())
        self.assertIsNone(view["summary"]["next_command"])
        self.assertIsNone(view["chat"]["input"])
        self.assertEqual("AutoCode could not start the model step", view["progress"]["headline"])
        self.assertEqual("nothing needed from you", view["progress"]["needs_you"])
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
        self.assertIn("failed attempts", explanation.stdout)
        self.assertNotIn("grant-recovery", explanation.stdout)

    def test_reconciliation_does_not_offer_a_retry_for_this_block(self):
        saved, run = self.exhausted()
        with patch("autocode_checkpoint_reconciliation.offer", return_value={"kind": "resume"}) as offer:
            view = run_view.view(saved, run_dir=run, workspace=Path(saved["workspace"]))
        offer.assert_not_called()
        self.assertIsNone(view["needs"])
        self.assertIsNone(view["next_command"])

    def test_a_stale_but_authenticated_request_is_still_reported_without_asking(self):
        saved, run = self.exhausted()
        saved["active_seconds"] += 1
        self.assertIsNone(human.current(saved))
        self.assertIsNotNone(human.retained(saved))
        view = run_view.view(saved, run_dir=run, workspace=Path(saved["workspace"]))
        self.assertIsNone(view["needs"])
        self.assertEqual("blocked", view["recovery"]["category"])

    def test_a_tampered_receipt_does_not_claim_a_classified_failure(self):
        import autocode_startup_timeout_view as startup_view

        saved, _ = self.exhausted()
        self.assertIsNotNone(startup_view.presentation(saved))
        saved[human.PUBLIC]["request"]["discovered"] = "tampered"
        self.assertIsNone(startup_view.presentation(saved))

    def test_altered_timeout_history_cannot_be_classified_by_an_old_receipt(self):
        import autocode_startup_timeout_view as startup_view

        saved, _ = self.exhausted(stall="during_reasoning_or_output")
        for row in saved["automatic_timeout_recoveries"]:
            row["stall_phase"] = "before_first_event"
        self.assertIsNotNone(human.retained(saved), "the old receipt remains readable")
        self.assertIsNone(startup_view.presentation(saved), "unbound state cannot classify the old stop")

    def test_other_operational_stops_and_explicit_time_limits_keep_their_existing_actions(self):
        for stall, pause in (
            ("during_reasoning_or_output", "PAUSED_TIMEOUT_RECOVERY"),
            ("before_first_event", "PAUSED_TIME_LIMIT"),
        ):
            with self.subTest(stall=stall, pause=pause):
                # Each subcase needs a fresh authenticated publication.
                self.setUp()
                saved, run = self.exhausted(stall=stall, pause=pause)
                view = run_view.view(saved, run_dir=run, workspace=Path(saved["workspace"]))
                self.assertEqual("answer", view["needs"]["kind"])
                self.assertIsNotNone(view["next_command"])


if __name__ == "__main__":
    unittest.main()
