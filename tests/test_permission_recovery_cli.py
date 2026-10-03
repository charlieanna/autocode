"""Permission recurrence through the public CLI, never injected runner state."""
import json
from pathlib import Path

from scenarios.harness.adversarial import AdversarialCase


class PermissionRecoveryCLI(AdversarialCase):
    def test_corrected_diagnostic_retains_work_and_reaches_independent_verification(self):
        self.set_fault("recovery", "builder_permission_corrected")
        view = self.driver.drive(self.scenario.brief)
        self.assertEqual(1, len(self.trace("builder_permission_denied")))
        corrected = self.trace("builder_permission_corrected")
        self.assertEqual(1, len(corrected), self.root)
        self.assertEqual("external_directory", corrected[0]["denied_operation"]["capability"])
        self.assertEqual("corrected diagnostic executed\n", Path(corrected[0]["receipt"]).read_text())
        self.assertTrue(self.trace("stage_enter", "sol"), "A diagnostic is not independent validation")
        self.assertTrue(self.trace("stage_enter", "astra_review"), "Completion must run normally")
        self.assertEqual("TASK_COMPLETE", view["status"], view)

    def test_repeated_denial_holds_after_one_retry_and_survives_restart(self):
        self.set_fault("recovery", "builder_permission_repeated")
        view = self.driver.drive(self.scenario.brief)
        self.assertEqual(2, len(self.trace("builder_permission_denied")), self.root)
        self.assertFalse(view["done"])
        self.assertIn("external_directory", json.dumps(view))
        self.assertIn("repeated", json.dumps(view).lower())
        self.assertEqual("# retained partial implementation\n", (self.project / "greet.py").read_text())
        state_before = self.driver.state()  # evidence only, never used to drive the CLI
        recovery_before = state_before["automatic_recoveries_since_resume"]
        archived = [Path(row["events"]) for row in state_before["stages"] if row.get("abandoned")]
        pins = {str(path): path.read_bytes() for path in archived}
        self.assertEqual(2, len(archived))
        for _ in range(2):
            self.invoke("--resume-paused")
            self.assertFalse(self.status()["done"])
        self.assertEqual(2, len(self.trace("builder_permission_denied")), "Restart minted another retry")
        self.assertEqual(recovery_before, self.driver.state()["automatic_recoveries_since_resume"])
        self.assertEqual(pins, {str(path): path.read_bytes() for path in archived})
        self.assertFalse(self.trace("stage_enter", "sol"))

    def test_successful_diagnostic_cannot_complete_a_broken_product(self):
        config = json.loads(self.config_path.read_text())
        config["reference"] = str(self.scenario.dir / "broken" / "accepts-two-names")
        self.config_path.write_text(json.dumps(config))
        self.set_fault("recovery", "builder_permission_corrected")
        view = self.driver.drive(self.scenario.brief)
        self.assertTrue(self.trace("builder_permission_corrected"), self.root)
        self.assertTrue(self.trace("stage_enter", "sol"))
        self.assertFalse(view["done"], "Successful scratch work cannot hide failed acceptance tests")
