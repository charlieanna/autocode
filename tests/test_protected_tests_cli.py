"""A weakened protected test through the public CLI, never injected runner state (GitHub issue #229)."""
import json

from scenarios.harness.adversarial import AdversarialCase

WEAKENED = "tests/test_shipping.py"


class ProtectedTestsCLI(AdversarialCase):
    scenario_id = "feature-protected-test"

    def deliver(self, solution):
        config = json.loads(self.config_path.read_text())
        config["reference"] = str(self.scenario.dir / solution)
        self.config_path.write_text(json.dumps(config))

    def test_weakened_test_is_refused_until_the_user_approves_that_exact_edit(self):
        self.deliver("broken/edits-threshold-test")
        view = self.driver.drive(self.scenario.brief)
        self.assertFalse(view["done"], view)
        self.assertIn("test_free_from_the_threshold", json.dumps(view))
        edited = (self.project / WEAKENED).read_text()
        self.assertIn("assertEqual(500, shipping_cents(5000))", edited, "The Builder's edit is retained")

        self.invoke("--resume-paused")
        view = self.driver.until_stopped()
        self.assertFalse(view["done"], "A restart without approval must not complete")

        refused = self.invoke("--approve-test-change", "shop/shipping.py")
        self.assertEqual(2, refused.returncode, refused.stdout + refused.stderr)
        self.assertFalse(self.status()["done"])

        approved = self.invoke("--approve-test-change", WEAKENED)
        self.assertEqual(0, approved.returncode, approved.stdout + approved.stderr)
        self.invoke("--resume-paused")
        view = self.driver.until_stopped()
        self.assertTrue(view["done"], view)
        self.assertEqual(edited, (self.project / WEAKENED).read_text())
        events = [row for row in self.driver.state()["user_events"] if row.get("kind") == "test_change_approval"]
        self.assertEqual([WEAKENED], [row["path"] for row in events])

    def test_keeping_the_original_tests_completes_without_approval(self):
        view = self.driver.drive(self.scenario.brief)
        self.assertTrue(view["done"], view)
        self.assertNotIn("--approve-test-change", json.dumps(view))
