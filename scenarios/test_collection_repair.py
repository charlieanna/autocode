"""A feature's tests that import what it adds prove it on the first Builder pass.

The greeting tests import ``greet`` at module level, so they cannot load on the original (empty) code.
They did not pass there and pass with the change, which is the feature's proof (owner-delegated decision
2026-11-24): the runner sends no "could not collect" feedback, and the scripted repair is never used.
"""

from __future__ import annotations

import shutil

from scenarios.harness.adversarial import AdversarialCase
from scenarios.harness.attack_collection_repair import loadable_greeting_tests


class CollectionRepairScenario(AdversarialCase):
    timeout_seconds = 300

    def test_tests_that_import_the_new_module_prove_it_without_a_repair(self):
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
        self.assertEqual(
            (self.scenario.reference / "test_greet.py").read_bytes(), (self.project / "test_greet.py").read_bytes()
        )
        proof = view["evidence"]["regression_proof"]
        self.assertEqual("PASS", proof["verdict"], proof)
        self.assertTrue(proof["fail_to_pass"], proof)
        findings = [
            row
            for row in view["evidence"]["findings"]
            if "base test run could not collect" in row.get("finding", "").lower()
        ]
        self.assertEqual([], findings, view)
