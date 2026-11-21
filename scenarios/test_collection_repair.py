"""A real task-run retains a rejected proof and lets the Builder repair its tests."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from scenarios.harness.adversarial import AdversarialCase


class CollectionRepairScenario(AdversarialCase):
    timeout_seconds = 300

    def test_builder_repairs_only_after_runner_feedback(self):
        repaired = self.root / "repaired-reference"
        shutil.copytree(self.scenario.reference, repaired)
        tests = repaired / "test_greet.py"
        text = tests.read_text()
        for anchor in ("from greet import greet\n", "    def test_greet(self):\n", "    def test_unicode(self):\n"):
            self.assertEqual(1, text.count(anchor), anchor)
        text = text.replace("from greet import greet\n", "")
        text = text.replace(
            "    def test_greet(self):\n", "    def test_greet(self):\n        from greet import greet\n"
        )
        text = text.replace(
            "    def test_unicode(self):\n", "    def test_unicode(self):\n        from greet import greet\n"
        )
        tests.write_text(text)
        self.env["SCENARIO_FAKE_REWORK_SOLUTION"] = str(repaired)
        self.set_fault("collection_repair", "repair_after_collection")
        self.approve(self.start_to_approval())
        view = self.finish()
        trace = self.trace()
        entries = [
            index
            for index, row in enumerate(trace)
            if row.get("event") == "stage_enter" and row.get("stage") == "builder"
        ]
        feedback = [index for index, row in enumerate(trace) if row.get("event") == "collection_feedback_observed"]
        self.assertGreaterEqual(len(entries), 2, trace)
        self.assertTrue(any(entries[1] < index for index in feedback), trace)
        self.assert_verified_delivery(view)
        self.assertEqual(tests.read_bytes(), (self.project / "test_greet.py").read_bytes())
        proof = view["evidence"]["regression_proof"]
        self.assertEqual("PASS", proof["verdict"], proof)
        self.assertTrue(proof["fail_to_pass"], proof)
        findings = [
            row
            for row in view["evidence"]["findings"]
            if "base test run could not collect" in row.get("finding", "").lower()
        ]
        self.assertEqual(1, len(findings), view)
        self.assertEqual("resolved", findings[0]["status"])
        prefix = "Runner regression proof receipt: "
        self.assertTrue(findings[0]["evidence"].startswith(prefix), findings)
        path = Path(findings[0]["evidence"][len(prefix) :])
        self.assertTrue(path.resolve().is_relative_to(self.driver.run_dir.resolve()))
        first = json.loads(path.read_text())
        self.assertEqual("FAIL", first["verdict"])
        self.assertEqual([], first["fail_to_pass"])
        self.assertNotEqual(first["source_revision"], proof["source_revision"])
