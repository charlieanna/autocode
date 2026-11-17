"""Permission recurrence through the public CLI, never injected runner state."""

import json
from pathlib import Path

from scenarios.harness.adversarial import AdversarialCase
from tests import operational_fixtures


class PermissionRecoveryCLI(AdversarialCase):
    # The ceiling case exercises four denied calls plus the attempts to resume the hold.
    timeout_seconds = 300

    def test_corrected_diagnostic_retains_work_and_reaches_independent_verification(self):
        self.set_fault("recovery", "builder_permission_corrected")
        view = self.driver.drive(self.scenario.brief)
        self.assertEqual(1, len(self.trace("builder_permission_denied")))
        corrected = self.trace("builder_permission_corrected")
        self.assertEqual(1, len(corrected), self.root)
        self.assertEqual("external_directory", corrected[0]["denied_operation"]["capability"])
        self.assertEqual("corrected diagnostic executed\n", Path(corrected[0]["receipt"]).read_text())
        self.assertTrue(self.trace("stage_enter", "tester"), "A diagnostic is not independent validation")
        self.assertTrue(self.trace("stage_enter", "review_completion"), "Completion must run normally")
        self.assertEqual("TASK_COMPLETE", view["status"], view)

    def test_repeated_denial_holds_after_one_retry_and_survives_restart(self):
        self.set_fault("recovery", "builder_permission_repeated")
        view = self.driver.drive(self.scenario.brief)
        self.assertEqual(2, len(self.trace("builder_permission_denied")), self.root)
        operational_fixtures.assert_view_blocked(self, view, "PAUSED_REPEATED_FAILURE")
        self.assertIn("repeatedly tried to access a folder outside the workspace", view["stop_reason"])
        self.assertIn("Repeated external_directory denial", operational_fixtures.kept_cause(self.driver.run_dir))
        self.assertFalse(self.trace("stage_enter", "investigate_stuck"))
        self.assertEqual("# retained partial implementation\n", (self.project / "greet.py").read_text())
        state_before = self.driver.state()  # evidence only, never used to drive the CLI
        recovery_before = state_before.get("automatic_recoveries_since_resume", 0)
        archived = [Path(row["events"]) for row in state_before["stages"] if row.get("abandoned")]
        pins = {str(path): path.read_bytes() for path in archived}
        self.assertEqual(2, len(archived))
        for _ in range(2):
            self.invoke("--resume-paused")
            self.assertFalse(self.status()["done"])
        self.assertEqual(2, len(self.trace("builder_permission_denied")), "Restart minted another retry")
        self.assertEqual(recovery_before, self.driver.state().get("automatic_recoveries_since_resume", 0))
        self.assertEqual(pins, {str(path): path.read_bytes() for path in archived})
        self.assertFalse(self.trace("stage_enter", "tester"))

    def test_distinct_denials_never_exhaust_the_timeout_budget_and_hold_at_their_own_ceiling(self):
        self.set_fault("recovery", "builder_permission_distinct")
        view = self.driver.drive(self.scenario.brief)
        operational_fixtures.assert_view_blocked(self, view, "PAUSED_REPEATED_FAILURE")
        surfaced = json.dumps(view)
        self.assertNotIn(
            "PAUSED_TIMEOUT_RECOVERY", surfaced, "permission denials must not surface as exhausted timeout recovery"
        )
        state = self.driver.state()
        self.assertEqual(
            0,
            state.get("automatic_recoveries_since_resume", 0),
            "a denial retry must not consume the shared timeout-recovery budget",
        )
        self.assertEqual(0, state.get("no_progress_batches", 0), "a denied call did not complete a Builder batch")
        denials = self.trace("builder_permission_denied")
        self.assertEqual([1, 2, 3, 4], [row["invocation"] for row in denials], self.root)
        self.assertIn("retry limit after trying to access folders outside the workspace", view["stop_reason"])
        self.assertIn("permission-recovery ceiling", operational_fixtures.kept_cause(self.driver.run_dir))
        self.assertFalse(self.trace("stage_enter", "investigate_stuck"))
        before = self.driver.state().get("automatic_recoveries_since_resume", 0)
        for flags in (("--resume-paused",), ("--resume-paused", "--retry-failed-stage")):
            self.invoke(*flags)
            self.assertFalse(self.status()["done"])
        self.assertEqual(
            before,
            self.driver.state().get("automatic_recoveries_since_resume", 0),
            "restarts must not mint timeout-recovery allowance either",
        )
        self.assertEqual(
            len(denials),
            len(self.trace("builder_permission_denied")),
            "restarts must not launch further denial retries",
        )

    def test_denial_still_counts_toward_an_explicit_no_progress_limit(self):
        self.driver.flags += ["--no-progress-limit", "1"]
        self.set_fault("recovery", "builder_permission_distinct")
        view = self.driver.drive(self.scenario.brief)
        self.assertEqual("PAUSED_NO_PROGRESS", view["status"])
        self.assertEqual(1, view["needs"]["no_progress_batches"])
        self.assertEqual("--resume-paused --no-progress-limit N", view["needs"]["action"])
        self.assertEqual(1, len(self.trace("builder_permission_denied")))

    def test_successful_diagnostic_cannot_complete_a_broken_product(self):
        config = json.loads(self.config_path.read_text())
        config["reference"] = str(self.scenario.dir / "broken" / "accepts-two-names")
        self.config_path.write_text(json.dumps(config))
        self.set_fault("recovery", "builder_permission_corrected")
        view = self.driver.drive(self.scenario.brief)
        self.assertTrue(self.trace("builder_permission_corrected"), self.root)
        self.assertTrue(self.trace("stage_enter", "tester"))
        self.assertFalse(view["done"], "Successful scratch work cannot hide failed acceptance tests")
