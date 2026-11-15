"""Operational exhaustion is a reported failure, not a machinery question."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode as runner
import autocode_operational_exhaustion_view as operational
import autocode_run_failure_view as run_failure
import autocode_run_view as run_view

from . import test_startup_timeout_person_view as startup_fixture


class OperationalExhaustionPersonViewTests(unittest.TestCase):
    setUp = startup_fixture.StartupTimeoutPersonViewTests.setUp
    contract = startup_fixture.StartupTimeoutPersonViewTests.contract
    exhausted = startup_fixture.StartupTimeoutPersonViewTests.exhausted

    def test_unresolved_internal_holds_do_not_ask_the_person_for_recovery(self):
        for pause, stall in (
            ("PAUSED_TIMEOUT_RECOVERY", "during_reasoning_or_output"),
            ("PAUSED_REPORT_REPAIR_LIMIT", "before_first_event"),
            ("PAUSED_PROVIDER_UNCERTAIN", "before_first_event"),
            ("PAUSED_UNCERTAIN_STAGE", "before_first_event"),
            ("PAUSED_REPEATED_FAILURE", "before_first_event"),
        ):
            with self.subTest(pause=pause):
                self.setUp()
                saved, run = self.exhausted(stall=stall, pause=pause)
                view = run_view.view(saved, run_dir=run, workspace=Path(saved["workspace"]))
                self.assertEqual("blocked", view["recovery"]["category"])
                self.assertIsNone(view["needs"])
                self.assertIsNone(view["next_command"])
                self.assertIsNone(view["chat"]["input"])
                self.assertFalse(view["done"])

    def test_spending_and_time_boundaries_keep_their_existing_actions(self):
        for pause in ("PAUSED_COST_LIMIT", "PAUSED_TIME_LIMIT", "PAUSED_ITERATION_LIMIT"):
            with self.subTest(pause=pause):
                self.setUp()
                saved, run = self.exhausted(pause=pause)
                view = run_view.view(saved, run_dir=run, workspace=Path(saved["workspace"]))
                self.assertEqual("answer", view["needs"]["kind"])
                self.assertIsNotNone(view["next_command"])

    def test_reconciliation_takes_precedence_over_reporting_a_failure(self):
        saved, run = self.exhausted(stall="during_reasoning_or_output")
        with patch.object(operational.validation, "pending", return_value={"scope": "repair"}):
            self.assertIsNone(run_failure.presentation(saved))
            view = run_view.view(saved, run_dir=run, workspace=Path(saved["workspace"]))
        self.assertEqual("continue", view["needs"]["kind"])
        self.assertIsNotNone(view["next_command"])

    def test_tampered_request_cannot_be_reported_as_authenticated_exhaustion(self):
        saved, _ = self.exhausted(pause="PAUSED_REPORT_REPAIR_LIMIT")
        saved["resolver_human_request"]["request"]["discovered"] = "altered"
        self.assertIsNone(operational.presentation(saved))

    def test_an_active_resolver_repair_is_not_reported_as_blocked(self):
        saved, _ = self.exhausted(pause="PAUSED_REPORT_REPAIR_LIMIT")
        saved.update(status="RUNNING", next_stage="resolve_repair", resolve_repair_request={"kind": "report_repair"})
        self.assertIsNone(run_failure.presentation(saved))

    def test_public_status_and_explanation_report_an_unresolved_repair(self):
        saved, run = self.exhausted(pause="PAUSED_REPORT_REPAIR_LIMIT")
        with tempfile.TemporaryDirectory() as home:
            base = [sys.executable, runner.__file__, "--workspace", saved["workspace"], "--run-dir", str(run)]
            env = {**os.environ, "AUTOCODE_HOME": home}
            status = subprocess.run([*base, "--status"], capture_output=True, text=True, check=True, env=env)
            explanation = subprocess.run([*base, "--explain"], capture_output=True, text=True, check=True, env=env)
        view = json.loads(status.stdout)["view"]
        self.assertEqual("blocked", view["summary"]["state"])
        self.assertIsNone(view["summary"]["next_command"])
        self.assertIsNone(view["chat"]["input"])
        self.assertIn("could not safely continue", explanation.stdout)
        self.assertNotIn("corrective information", explanation.stdout)


if __name__ == "__main__":
    unittest.main()
