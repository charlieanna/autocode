"""Planning policies must respect the fields owned by the report being requested."""

import copy
import json
import unittest
from unittest.mock import patch

import autocode_planning_report_policy as policy
import autocode_util as util
from units import autoplanner as planner

from . import test_plan_retention as retention_fixtures
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


class ConcernGenerationTests(unittest.TestCase):
    def request(self, stage, concerns=None, *, retained=False):
        state = fixed_state()
        if retained:
            state.update(retention_fixtures.PlanRetentionTests().state())
        v2 = stage.startswith("plan_")
        if v2:
            state["settings"]["planning_flow"] = "v2"
            state["settings"]["roles"]["planner"] = {"model": "fixed-model", "engine": "codex"}
        review_stage = "plan_review" if v2 else "astra_challenge"
        if concerns is not None:
            state.setdefault("planning", {}).setdefault("reports", {})[review_stage] = {
                "report": {"concerns": concerns}
            }
        with patch.object(planner.artifacts, "verify_predecessor", return_value=None):
            return planner.prepare(state, stage, STATE_PATH, None)

    def row(self, field, concern_id):
        if field == "responses":
            return {
                "concern_id": concern_id,
                "response": "Accepted: the missing boundary is covered by AC6.",
                "evidence_refs": ["pager.py:8"],
                "change": "Added AC6 within the existing milestone.",
                "acceptance_test": "AC6's named restore test fails before and passes after the fix.",
            }
        return {
            "concern_id": concern_id,
            "decision": "Resolved by AC6.",
            "rationale": "The exact boundary example distinguishes integer and floating-point results.",
            "acceptance_test": "AC6's named restore test fails before and passes after the fix.",
            "resolved": True,
        }

    def test_prepared_requests_bind_answer_rows_to_the_current_review(self):
        for stage, field in (
            ("glm_revise", "responses"),
            ("astra_finalize", "decisions"),
            ("plan_revise", "responses"),
            ("plan_finalize", "decisions"),
        ):
            with self.subTest(stage=stage):
                ordinary = self.request(stage).schema
                request = self.request(stage, [{"id": "C1"}, {"id": "C2"}])
                rows = request.schema["properties"][field]
                good = [self.row(field, "C1"), self.row(field, "C2")]
                util.validate_schema(good, rows)
                with self.assertRaisesRegex(ValueError, "invalid enum"):
                    util.validate_schema([good[0], self.row(field, "AUDIT-HANDOFF")], rows)
                with self.assertRaisesRegex(ValueError, "too few"):
                    util.validate_schema(good[:1], rows)
                with self.assertRaisesRegex(ValueError, "too many"):
                    util.validate_schema([*good, self.row(field, "AUDIT-PROVENANCE")], rows)
                # Generation changes only this field; contract, observation and blocker schemas remain exact.
                expected = copy.deepcopy(ordinary)
                expected["properties"][field]["minItems"] = 2
                expected["properties"][field]["maxItems"] = 2
                expected_props = expected["properties"][field]["items"]["properties"]
                expected_props["concern_id"] = {**expected_props["concern_id"], "enum": ["C1", "C2"]}
                self.assertEqual(expected, request.schema)
                self.assertNotIn(
                    "enum", planner.SCHEMAS[stage]["properties"][field]["items"]["properties"]["concern_id"]
                )
                self.assertIn("required review or investigation", request.prompt)
                self.assertIn("contract.open_blocking_questions", request.prompt)
                self.assertIn("CONCERN ROWS", planner.repair_rules(stage, request.schema))

    def test_retained_revision_and_final_review_keep_their_distinct_blocker_forms(self):
        review = [{"id": "C1"}]
        revision = self.request("glm_revise", review, retained=True)
        forms = revision.schema["properties"]["contract"]["anyOf"]
        reference = next(form for form in forms if "revise_contract" in form["properties"])
        full = next(form for form in forms if "open_blocking_questions" in form["properties"])
        self.assertNotIn("open_blocking_questions", reference["properties"])
        self.assertNotIn("open_blocking_questions", reference["properties"]["changes"]["properties"])
        self.assertIn("open_blocking_questions", full["required"])
        final = self.request("astra_finalize", review, retained=True)
        self.assertIn("open_blocking_questions", final.schema["properties"]["contract"]["properties"])
        for request, field in ((revision, "responses"), (final, "decisions")):
            self.assertEqual(["C1"], request.schema["properties"][field]["items"]["properties"]["concern_id"]["enum"])
            self.assertIn(
                "Use the full contract form if the offered revision reference cannot carry that field", request.prompt
            )
        self.assertIn("Audit every rule in contract_author_rules", final.prompt)

    def test_loader_projection_changes_only_bound_planning_answer_constraints(self):
        generated = self.request("astra_finalize", [{"id": "C1"}], retained=True).schema
        original = copy.deepcopy(generated)
        accepted = policy.concern_validation_schema(generated, {"stage": "astra_finalize"})
        expected = copy.deepcopy(generated)
        rows = expected["properties"]["decisions"]
        rows.pop("minItems")
        rows.pop("maxItems")
        rows["items"]["properties"]["concern_id"].pop("enum")
        self.assertEqual(expected, accepted)
        self.assertEqual(original, generated)
        for stage in ("sol", "astra_review", "terra"):
            self.assertEqual(generated, policy.concern_validation_schema(generated, {"stage": stage}))
        without_contract = copy.deepcopy(generated)
        without_contract["properties"].pop("contract")
        self.assertEqual(
            without_contract, policy.concern_validation_schema(without_contract, {"stage": "astra_finalize"})
        )
        self.assertEqual(
            accepted,
            policy.concern_validation_schema(
                generated, {"stage": "astra_finalize_report_repair", "original_stage": "astra_finalize"}
            ),
        )

    def test_empty_review_requests_empty_rows_but_absent_review_does_not(self):
        for stage, field in (("glm_revise", "responses"), ("astra_finalize", "decisions")):
            with self.subTest(stage=stage):
                rows = self.request(stage, []).schema["properties"][field]
                util.validate_schema([], rows)
                with self.assertRaisesRegex(ValueError, "too many"):
                    util.validate_schema([self.row(field, "AUDIT-HANDOFF")], rows)
                # An absent review cannot silently suppress its unknown concerns.
                self.assertNotIn("maxItems", self.request(stage).schema["properties"][field])


if __name__ == "__main__":
    unittest.main()
