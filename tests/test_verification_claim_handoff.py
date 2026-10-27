"""Same-packet criterion references must retain every verification obligation."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_handoff_dedup as handoff
import autocode_stage_context as stage_context
import autocode_verification_plan as verification

from . import test_handoff_dedup as fixtures


def packet(stage="sol"):
    value = fixtures.packet(stage)
    value["verification_obligations"] = verification.obligations(value)
    return value


def expanded(value):
    result = copy.deepcopy(value)
    result.pop("handoff_reference_policy", None)
    if "acceptance_criteria_ref" in result:
        ref = result.pop("acceptance_criteria_ref")
        result["acceptance_criteria"] = [
            {key: row[key] for key in ref["fields"]} for row in fixtures.resolve(value, ref["path"])
        ]
    for check in (result.get("verification_obligations") or {}).get("checks", []):
        if isinstance(check, dict) and "claim_ref" in check:
            check["claim"] = fixtures.resolve(value, check.pop("claim_ref"))
    for check in ((result.get("validation") or {}).get("check_replay") or {}).get("checks", []):
        if "command_ref" in check:
            check["command"] = fixtures.resolve(value, check.pop("command_ref"))
    return result


class VerificationClaimHandoffTests(unittest.TestCase):
    def test_exact_claims_reference_the_complete_contract_and_round_trip_all_proof(self):
        for stage in ("terra", "sol", "astra_review", "astra_checkpoint"):
            with self.subTest(stage=stage):
                original = packet(stage)
                before = copy.deepcopy(original)
                compact = handoff.deduplicate(original)
                checks = compact["verification_obligations"]["checks"]
                self.assertTrue(checks)
                self.assertTrue(all("claim_ref" in row and "claim" not in row for row in checks))
                self.assertEqual(original, expanded(compact))
                self.assertEqual(original["goal_contract"], compact["goal_contract"])
                self.assertEqual(before, original)
                self.assertLess(len(json.dumps(compact)), len(json.dumps(original)))
                self.assertEqual(compact, handoff.deduplicate(compact))

    def test_unknown_or_changed_claim_bindings_remain_inline(self):
        for fault in (
            "unknown",
            "multiple",
            "duplicate",
            "text",
            "identity",
            "missing_identity",
            "source",
            "method",
            "purpose",
            "existing",
        ):
            with self.subTest(fault=fault):
                value = packet()
                row = value["verification_obligations"]["checks"][0]
                criterion = value["goal_contract"]["body"]["acceptance_criteria"][0]
                if fault == "unknown":
                    row["criterion_ids"] = ["unknown"]
                elif fault == "multiple":
                    row["criterion_ids"].append("C2")
                elif fault == "duplicate":
                    value["goal_contract"]["body"]["acceptance_criteria"].append(copy.deepcopy(criterion))
                elif fault == "text":
                    row["claim"] += " different required outcome"
                elif fault == "identity":
                    row["criterion_identity"] = "stale"
                elif fault == "missing_identity":
                    row.pop("criterion_identity")
                elif fault == "source":
                    criterion["sources"] = [{"source_id": "other", "quote": "different source"}]
                elif fault == "method":
                    criterion["verification_method"] = "Run a different required check"
                elif fault == "purpose":
                    row["purpose"] = "approved_task_check"
                elif fault == "existing":
                    row["claim_ref"] = "#/existing/custom/reference"
                expected = copy.deepcopy(row)
                self.assertEqual(expected, handoff.deduplicate(value)["verification_obligations"]["checks"][0])

    def test_legacy_short_malformed_and_nonexecution_packets_remain_unreferenced(self):
        for value in (
            {"stage": "sol", "verification_obligations": {"checks": [{"claim": "legacy"}]}},
            {"stage": "sol", "goal_contract": {"body": {"acceptance_criteria": []}}, "verification_obligations": None},
        ):
            self.assertEqual(value, handoff.deduplicate(value))
        for stage in ("astra_discovery", "astra_plan", "write_acceptance_tests"):
            value = packet(stage)
            self.assertEqual(value["verification_obligations"], handoff.deduplicate(value)["verification_obligations"])
        value = packet()
        value["verification_obligations"]["checks"] = [None, {"claim": "no identity"}]
        self.assertEqual(value["verification_obligations"], handoff.deduplicate(value)["verification_obligations"])
        value = packet()
        criterion = value["goal_contract"]["body"]["acceptance_criteria"][0]
        criterion["criterion"] = "Works"
        value["verification_obligations"] = verification.obligations(value)
        self.assertEqual("Works", handoff.deduplicate(value)["verification_obligations"]["checks"][0]["claim"])

    def test_effective_stage_prompt_contains_each_long_claim_once_and_keeps_full_contract(self):
        with tempfile.TemporaryDirectory() as root:
            value = packet()
            value.update(
                version=3,
                task="Implement all 18 requirements",
                workspace=root,
                settings={"roles": {role: {"engine": "codex", "model": "fake"} for role in ("astra", "terra", "sol")}},
            )
            value["answers"] = value["saved_answers"]
            before = copy.deepcopy(value)
            with patch.object(stage_context.support, "snapshot", return_value={"revision": "r", "head": "h"}):
                text, _ = stage_context.context_packet(value, "sol", Path(root) / "state.json")
            data = json.loads(text.split("CURRENT HANDOFF DATA\n", 1)[1])
            self.assertTrue(all("claim_ref" in row for row in data["verification_obligations"]["checks"]))
            self.assertEqual(1, text.count(value["goal_contract"]["body"]["acceptance_criteria"][0]["criterion"]))
            self.assertEqual(value["goal_contract"], data["goal_contract"])
            self.assertEqual(before, value)
