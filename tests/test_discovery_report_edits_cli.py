"""Discovery's existing report-edit encoding through the public CLI."""

import hashlib
import json
import unittest
from pathlib import Path

from . import test_stage_repair_cli as support


class DiscoveryReportEditsCLI(unittest.TestCase):
    maxDiff = None

    # Reuse the existing harness without discovering or rerunning unrelated tests.
    fixture_for = support.StageRepairCLI.fixture_for
    plan = support.StageRepairCLI.plan
    assert_approval_boundary = support.StageRepairCLI.assert_approval_boundary

    def check_edit(self, mode):
        fixture = self.fixture_for("discovery-edits-" + mode)
        run, state, view, trace = self.plan(fixture)
        self.assert_approval_boundary(fixture, state, view, trace)
        repairs = [row for row in trace if row["repair"]]
        self.assertEqual(1, len(repairs), repairs)
        repair = repairs[0]
        self.assertEqual("astra_discovery", repair["stage"])
        self.assertTrue(repair["offered"])
        self.assertTrue(repair["rule"])
        output = Path(repair["output"])
        self.assertEqual(repair["patch"], json.loads(output.with_suffix(".reported.json").read_text()))
        source = Path(repair["source_path"]).read_bytes()
        self.assertEqual(repair["source_hash"], hashlib.sha256(source).hexdigest())
        self.assertLess(len(json.dumps(repair["patch"]).encode()), len(source))
        fixed = json.loads(output.read_text())
        if mode == "object":
            expected = json.loads(source)
            del expected["contract"]["contract_changes_placeholder"]
        else:
            expected = json.loads(source.decode().replace('"summary":', ',"summary":', 1))
        # Existing loader defaults are outside the model-authored edit; assert
        # their exact values while checking every retained report field.
        expected["contract"].setdefault("git_delivery", None)
        for criterion in expected["contract"]["acceptance_criteria"]:
            criterion.setdefault("current_behavior", "")
        self.assertEqual(expected, fixed)
        shown = fixture.launch(["--run-dir", str(run), "--show-goal"], 0).stdout
        self.assertIn("Greet", shown)
        self.assertIn("C1", shown)

    def test_discovery_object_edits_reach_exact_plan_gate(self):
        self.check_edit("object")

    def test_discovery_text_edits_reach_exact_plan_gate(self):
        self.check_edit("text")

    def test_discovery_edits_reject_stale_source(self):
        self.check_refusal("stale", "exact rejected source")

    def test_discovery_edits_cannot_invent_user_authority(self):
        self.check_refusal("authority", "actual saved user event")

    def check_refusal(self, fault, expected):
        for mode in ("object", "text"):
            with self.subTest(mode=mode):
                fixture = self.fixture_for("discovery-edits-" + mode + "-" + fault)
                _, _, view, trace = self.plan(fixture)
                self.assertNotEqual("AWAITING_GOAL_APPROVAL", view["status"])
                self.assertFalse(view["done"])
                self.assertFalse(any(row["stage"] == "terra" for row in trace))
                repairs = [row for row in trace if row["repair"]]
                self.assertTrue(repairs)
                self.assertTrue(repairs[0]["offered"])
                self.assertTrue(any(expected in row.get("error", "") for row in trace), trace)
