"""Planning policies must respect the fields owned by the report being requested."""

import copy
import json
import unittest
from unittest.mock import patch

import autocode_util as util
from units import autoplanner as planner

from .test_prompt_rendering import STATE_PATH, fixed_state


class PlanningReportPolicyTests(unittest.TestCase):
    def render(self, stage):
        state = fixed_state()
        before = copy.deepcopy(state)
        with patch.object(planner.artifacts, "verify_predecessor", return_value=None):
            prompt, _ = planner.context(state, stage, STATE_PATH)
        self.assertEqual(before, state)
        instruction, raw = prompt.split("\nCURRENT HANDOFF DATA\n", 1)
        return instruction, json.loads(raw)

    def test_concern_only_reviews_audit_contract_rules_without_authoring_commands(self):
        expected = {
            "decision_provenance": planner.goals.DECISION_PROVENANCE,
            "contract_references": planner.goals.CONTRACT_REFERENCES,
            "revision_conflicts": planner.REVISION_CONFLICT_RULE,
            "example_criteria": planner.EXAMPLE_CRITERIA_RULE,
            "milestone_handoff": planner.s.MILESTONE_POLICY,
        }
        for stage in ("astra_challenge", "plan_review"):
            with self.subTest(stage=stage):
                instruction, packet = self.render(stage)
                for command in (
                    "Otherwise return delegated_decisions=[]",
                    "Use contract_changes=[]",
                    "Set contract.initial_task.kind=none",
                    "write every acceptance criterion",
                ):
                    self.assertFalse(command in instruction, f"{stage} still instructs a forbidden action: {command}")
                self.assertEqual(expected, packet["contract_author_rules"])
                self.assertIn("Audit every rule in contract_author_rules", instruction)
                self.assertIn("blocking concern", instruction)
                self.assertIn("do not return or edit a contract", instruction)
                self.assertIn(
                    "observation changes, source_trace or obligation_decisions defined by that schema", instruction
                )

    def test_contract_authors_keep_their_existing_commands(self):
        for stage in ("astra_discovery", "glm_revise", "astra_finalize", "plan", "plan_revise", "plan_finalize"):
            with self.subTest(stage=stage):
                instruction, packet = self.render(stage)
                self.assertIn(planner.goals.DECISION_PROVENANCE, instruction)
                self.assertIn(planner.goals.CONTRACT_REFERENCES, instruction)
                self.assertIn(planner.REVISION_CONFLICT_RULE, instruction)
                self.assertIn(planner.EXAMPLE_CRITERIA_RULE, instruction)
                self.assertNotIn("contract_author_rules", packet)

    def test_forbidden_contract_fields_still_fail_the_closed_review_schema(self):
        report = {"summary": "Reviewed the proposed contract", "concerns": []}
        for stage in ("astra_challenge", "plan_review"):
            schema = planner.SCHEMAS[stage]
            util.validate_schema(report, schema)
            for field, value in (("contract", {}), ("contract_changes", []), ("delegated_decisions", [])):
                with self.subTest(stage=stage, field=field), self.assertRaisesRegex(ValueError, "unexpected fields"):
                    util.validate_schema({**report, field: value}, schema)


if __name__ == "__main__":
    unittest.main()
