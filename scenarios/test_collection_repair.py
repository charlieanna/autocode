"""A real task-run accepts a first suite's test that cannot load on the base only because the feature is absent."""

from __future__ import annotations

import shutil

from scenarios.harness.adversarial import AdversarialCase
from scenarios.harness.attack_collection_repair import loadable_greeting_tests


class CollectionRepairScenario(AdversarialCase):
    timeout_seconds = 300

    def test_test_importing_the_module_the_change_adds_needs_no_repair(self):
        # The reference test imports greet at module level, and greet.py is what the change adds, so the
        # test cannot load on the base. That is the feature's absence, not a defect for the Builder to
        # repair (owner decision 2026-11-24; the first-suite rule, #4146): the first proof is accepted.
        # The repair fault stays installed, so a collection finding would hand the Builder a rework.
        repaired = self.root / "repaired-reference"
        shutil.copytree(self.scenario.reference, repaired)
        tests = repaired / "test_greet.py"
        tests.write_text(loadable_greeting_tests(tests.read_text()))
        self.env["SCENARIO_FAKE_REWORK_SOLUTION"] = str(repaired)
        self.set_fault("collection_repair", "repair_after_collection")
        self.approve(self.start_to_approval())
        view = self.finish()
        trace = self.trace()
        builders = [row for row in trace if row.get("event") == "stage_enter" and row.get("stage") == "builder"]
        self.assertEqual(1, len(builders), trace)
        self.assertFalse([row for row in trace if row.get("event") == "collection_feedback_observed"], trace)
        self.assert_verified_delivery(view)
        reference = self.scenario.reference / "test_greet.py"
        self.assertEqual(reference.read_bytes(), (self.project / "test_greet.py").read_bytes())
        self.assertIn("from greet import greet\n", reference.read_text())
        proof = view["evidence"]["regression_proof"]
        self.assertEqual("PASS", proof["verdict"], proof)
        self.assertTrue(proof["fail_to_pass"], proof)
        self.assertFalse(
            [
                row
                for row in view["evidence"]["findings"]
                if "base test run could not collect" in row.get("finding", "").lower()
            ],
            view,
        )
