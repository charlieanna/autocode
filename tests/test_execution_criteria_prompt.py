"""Execution instructions follow the report schema without restating the approved goal."""

import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_execution_prompt as execution_prompt
import autocode_goals as goals
import autocode_stage_context as context
import autocode_support as support
import autocode_workflow as workflow
from units import autoresolver, common

from .test_prompt_rendering import SNAPSHOT, STATE_PATH, fixed_state

SCHEMAS = Path(support.__file__).parent / "autocode-schemas"


class ExecutionCriteriaPromptTests(unittest.TestCase):
    def state(self):
        state = fixed_state()
        criteria = [
            {
                "id": "AC1",
                "criterion": "Print Hello exactly.",
                "verification_method": "Run greeting",
                "human_review": False,
            }
        ]
        state["goal_contract"] = {"revision": 2, "hash": "approved-hash", "body": {"acceptance_criteria": criteria}}
        state["acceptance_criteria"] = [
            {"id": "AC1", "criterion": "Print Hello exactly.", "status": "unverified", "evidence": ""}
        ]
        return state

    def prepare(self, state, stage):
        with (
            patch.object(goals, "execution_guard"),
            patch.object(context.source_scope, "snapshot", return_value=SNAPSHOT),
        ):
            return common.execution_request(state, stage, STATE_PATH, SCHEMAS)

    def test_builder_report_does_not_request_unsupported_criterion_echo(self):
        state = self.state()
        contract = copy.deepcopy(state["goal_contract"])
        request = self.prepare(state, "terra")
        instruction, raw = request.prompt.split("\nCURRENT HANDOFF DATA\n", 1)
        self.assertNotIn("acceptance_criteria", request.schema["properties"])
        self.assertNotIn("Restate acceptance_criteria entries", instruction)
        self.assertEqual(contract, json.loads(raw)["goal_contract"])
        self.assertEqual(contract, state["goal_contract"])
        for rule in (
            "Echo its revision and hash",
            "Echo current_task.id as task_id",
            "Do not weaken criteria",
            "Human approvals come only from runner user events",
            "Report changed files, addressed_requirements",
        ):
            self.assertIn(rule, instruction)

    def test_prepared_planner_and_completion_match_definition_and_id_only_outputs(self):
        state = self.state()
        contract = copy.deepcopy(state["goal_contract"])
        planner = self.prepare(state, "astra_plan")
        self.assertIn("definition fields byte-identical: id, criterion", planner.prompt)
        self.assertNotIn("definition fields byte-identical: id, criterion, verification_method", planner.prompt)
        completion = self.prepare(state, "astra_review")
        self.assertIn(
            "Report every approved criterion ID once, in approved order, in acceptance_criteria", completion.prompt
        )
        self.assertNotIn("definition fields byte-identical", completion.prompt)
        # Generation is ID-only; the saved/legacy schema still validates complete definitions.
        canonical = copy.deepcopy(completion.schema)
        generated = support.review_generation_schema(completion.schema, state, "astra_review")
        self.assertIn("criterion", canonical["properties"]["acceptance_criteria"]["items"]["properties"])
        self.assertNotIn("criterion", generated["properties"]["acceptance_criteria"]["items"]["properties"])
        self.assertEqual(canonical, completion.schema)
        self.assertEqual(contract, state["goal_contract"])
        self.assertEqual(generated, support.review_generation_schema(completion.schema, state, "astra_review"))

    def test_validator_and_final_only_builder_have_no_definition_output(self):
        for stage, mode in (("sol", None), ("terra", workflow.FINAL_MODE)):
            with self.subTest(stage=stage, mode=mode), patch.object(workflow, "guard"):
                state = self.state()
                if mode:
                    state["settings"]["workflow"] = {"mode": mode}
                request = self.prepare(state, stage)
                self.assertNotIn("definition fields byte-identical", request.prompt)
                self.assertNotIn("Report every approved criterion ID once", request.prompt)
                self.assertNotIn("Restate acceptance_criteria entries", request.prompt)
                self.assertIn("Do not weaken criteria", request.prompt)
                if mode:
                    self.assertIn("continuation", request.schema["properties"])
                else:
                    self.assertIn("criterion_results", request.schema["properties"])
                    self.assertIn("reports evidence for every criterion", request.prompt)

    def test_checkpoint_override_keeps_nested_decision_definitions(self):
        for mode in (None, workflow.MODE, workflow.FINAL_MODE):
            with self.subTest(mode=mode), patch.object(workflow, "guard"):
                state = self.state()
                if mode:
                    state["settings"]["workflow"] = {"mode": mode}
                request = self.prepare(state, "astra_checkpoint")
                definitions = request.schema["properties"]["decision"]["properties"]["acceptance_criteria"]
                self.assertIn("criterion", definitions["items"]["properties"])
                self.assertIn("Restate decision.acceptance_criteria entries", request.prompt)
                self.assertIn("definition fields byte-identical: id, criterion", request.prompt)
                self.assertNotIn("Report every approved criterion ID once", request.prompt)

    def test_resolver_rework_keeps_definitions_but_diagnosis_does_not(self):
        for stage in ("astra_resolve", "astra_diagnose"):
            with (
                self.subTest(stage=stage),
                patch.object(goals, "execution_guard"),
                patch.object(context.source_scope, "snapshot", return_value=SNAPSHOT),
                patch.object(autoresolver, "guard"),
                patch.object(autoresolver, "diagnosis_guard"),
            ):
                request = autoresolver.prepare(self.state(), stage, STATE_PATH, SCHEMAS)
                if stage == "astra_resolve":
                    self.assertIn(
                        "criterion", request.schema["properties"]["acceptance_criteria"]["items"]["properties"]
                    )
                    self.assertIn("definition fields byte-identical: id, criterion", request.prompt)
                else:
                    self.assertNotIn("acceptance_criteria", request.schema["properties"])
                    self.assertNotIn("definition fields byte-identical", request.prompt)
                self.assertNotIn("Report every approved criterion ID once", request.prompt)
                self.assertIn("Do not weaken criteria", request.prompt)

    def test_direct_context_keeps_full_approved_input_and_scope(self):
        state = self.state()
        contract = copy.deepcopy(state["goal_contract"])
        with patch.object(context.source_scope, "snapshot", return_value=SNAPSHOT):
            prompt, _ = context.context_packet(state, "terra", STATE_PATH)
        instructions, payload = prompt.split("\nCURRENT HANDOFF DATA\n", 1)
        self.assertEqual(contract, json.loads(payload)["goal_contract"])
        self.assertEqual(contract, state["goal_contract"])
        self.assertNotIn("Restate acceptance_criteria entries", instructions)
        self.assertIn("Do not weaken criteria", instructions)
        self.assertIn("Human approvals come only from runner user events", instructions)

    def test_schema_inspection_does_not_change_input_or_treat_task_ids_as_definitions(self):
        schema = workflow.implementation_schema(SCHEMAS)
        before = copy.deepcopy(schema)
        self.assertEqual("", execution_prompt.criteria_instruction(schema))
        self.assertEqual(before, schema)
        schema = goals.role_schema(support.read(SCHEMAS / "v2/astra-decision.schema.json"), "astra")
        before = copy.deepcopy(schema)
        note = execution_prompt.criteria_instruction(schema)
        self.assertIn("definition fields byte-identical: id, criterion", note)
        self.assertNotIn("next_task.acceptance_criteria", note)
        self.assertEqual(before, schema)


if __name__ == "__main__":
    unittest.main()
