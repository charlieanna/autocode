"""Acceptance-test prompts retain every obligation without repeating its text."""

import copy
import json
import unittest

import autocode_acceptance_tests as acceptance
import autocode_acceptance_tests_job as job
import autocode_handoff_dedup as handoff


def state():
    criteria = [
        {
            "id": f"C{i}",
            "criterion": f"Requirement {i}: " + "Preserve the exact command and reject foreign evidence. " * 30,
            "verification_method": f"{'guard' if i == 2 else 'test'}: test_requirement_{i}",
            "human_review": False,
            "sources": [{"source_id": "task:0", "quote": f"Original requirement {i}"}],
        }
        for i in range(3)
    ]
    result = {
        "task": "Implement the approved evidence change",
        "settings": {"spec_first": True},
        "goal_contract": {
            "hash": "approved-hash",
            "revision": 5,
            "approval_status": "approved",
            "body": {
                "acceptance_criteria": criteria,
                "constraints": ["Keep independent verification"],
                "permission_boundaries": ["Only edit the approved files"],
            },
        },
        "next_stage": "terra",
    }
    acceptance.route(result)
    return result


def expanded(packet):
    result = copy.deepcopy(packet)
    result.pop("handoff_reference_policy", None)
    for row in result.get("cases", []):
        reference = row.pop("criterion_ref", None)
        if reference:
            value = packet
            for key in reference["path"].removeprefix("#/").split("/"):
                value = value[int(key)] if isinstance(value, list) else value[key]
            row["criterion"] = reference["prefix"] + value
    return result


class AcceptanceHandoffTests(unittest.TestCase):
    def test_effective_prompt_keeps_complete_contract_and_each_case_once(self):
        original = state()
        before = copy.deepcopy(original)
        packet = job.packet(original)
        text, metrics = job.prompt(original)
        shown = json.loads(text.split("CURRENT HANDOFF DATA\n", 1)[1])
        self.assertEqual(3, len(shown["cases"]))
        self.assertTrue(all("criterion_ref" in row for row in shown["cases"]))
        self.assertEqual(packet, expanded(shown))
        self.assertEqual(before, original)
        self.assertEqual(original["goal_contract"], shown["goal_contract"])
        for row in original["goal_contract"]["body"]["acceptance_criteria"]:
            self.assertEqual(1, text.count(row["criterion"]))
        self.assertLess(len(json.dumps(shown)), len(json.dumps(packet)))
        self.assertEqual((len(text.encode()) + 3) // 4, metrics["estimated_prompt_tokens"])
        self.assertEqual(shown, handoff.deduplicate(shown))

    def test_repair_context_remains_self_contained(self):
        original = state()
        context = job.repair_context(original)
        self.assertEqual(job.packet(original)["cases"], context["cases"])
        self.assertTrue(all("criterion" in row and "criterion_ref" not in row for row in context["cases"]))

    def test_different_unknown_ambiguous_and_existing_references_stay_inline(self):
        for kind in ("different", "unknown", "ambiguous", "referenced", "missing_id"):
            with self.subTest(kind=kind):
                packet = job.packet(state())
                row = packet["cases"][0]
                if kind == "different":
                    row["criterion"] += " A distinct diagnosis obligation."
                elif kind == "unknown":
                    row["id"] = "DIAGNOSIS"
                elif kind == "ambiguous":
                    packet["goal_contract"]["body"]["acceptance_criteria"].append(
                        copy.deepcopy(packet["goal_contract"]["body"]["acceptance_criteria"][0])
                    )
                elif kind == "referenced":
                    row["criterion_ref"] = {"path": "#/external", "prefix": ""}
                else:
                    row.pop("id")
                compact = handoff.deduplicate(packet)
                self.assertEqual(row, compact["cases"][0])
                self.assertEqual(packet["goal_contract"], compact["goal_contract"])

    def test_shared_names_and_inherited_kinds_are_not_collapsed(self):
        packet = job.packet(state())
        packet["cases"][1]["test_name"] = packet["cases"][0]["test_name"]
        packet["cases"][1]["kind"] = "test: already delivered; retain fresh original-reference proof"
        packet["cases"].append({"id": "T1", "criterion": "Distinct diagnosis probe", "test_name": "test_probe"})
        compact = handoff.deduplicate(packet)
        self.assertEqual(4, len(compact["cases"]))
        self.assertEqual(packet, expanded(compact))

    def test_small_and_non_acceptance_packets_keep_their_shape(self):
        packet = job.packet(state())
        packet["cases"] = [{"id": "C1", "criterion": "C1: Works", "test_name": "test_one", "kind": "test"}]
        packet["goal_contract"]["body"]["acceptance_criteria"] = [{"id": "C1", "criterion": "Works"}]
        self.assertEqual(packet, handoff.deduplicate(packet))
        packet = job.packet(state())
        packet["stage"] = "astra_plan"
        self.assertEqual(packet, handoff.deduplicate(packet))
