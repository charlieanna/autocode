"""Every planning role receives the same limits on invented bug-fix scope."""

import copy
import json
import unittest
from unittest.mock import patch

import autocode_catalog_context as catalog_context
import ci_test_selection
from units import autoplanner as planner

from .test_prompt_rendering import STATE_PATH, fixed_state


class PlanningScopeDisciplineTests(unittest.TestCase):
    def test_reviewers_block_missing_observable_behavior_but_allow_implementation_details(self):
        for stage in ("review_plan", "finalize_plan", "plan_review", "plan_finalize"):
            for retained in (False, True):
                with self.subTest(stage=stage, retained=retained):
                    state = fixed_state()
                    state["phase"] = "PLANNING"
                    state["settings"]["adaptive_planning"] = True
                    if stage.startswith("plan_"):
                        state["settings"]["planning_flow"] = "v2"
                        state["settings"]["roles"]["planner"] = {"model": "fixed-model", "engine": "codex"}
                    if retained:
                        state["goal_contract"] = {
                            "revision": 1,
                            "hash": "saved-hash",
                            "body": {"acceptance_criteria": [{"id": "AC1", "criterion": "Preserve bytes."}]},
                        }
                    before = copy.deepcopy(state)
                    with (
                        patch.object(catalog_context, "context", return_value=None),
                        patch.object(planner, "workspace_inventory", return_value={"files": ["history.py"]}),
                        patch.object(planner.artifacts, "verify_predecessor", return_value=None),
                    ):
                        request = planner.prepare(state, stage, STATE_PATH, None)
                    instruction = " ".join(request.prompt.split("\nCURRENT HANDOFF DATA\n", 1)[0].split())
                    for rule in (
                        "OBSERVABLE PLAN COHERENCE",
                        "existing source or an explicit planned change",
                        "must invent an additional behavior",
                        "blocking concern",
                        "ordinary implementation details",
                        "Optional polish may remain non-blocking",
                    ):
                        self.assertTrue(rule in instruction, f"{stage} lacks {rule!r}")
                    self.assertEqual(before, state)

    def test_example_check_prompt_selects_its_rendered_request_regression(self):
        self.assertIn(
            "tests.test_planning_scope_discipline",
            ci_test_selection.dependencies("tools/prompts/fragments/autoplanner/example-check-rule.md"),
        )

    def test_scope_prompt_selects_its_rendered_request_regression(self):
        self.assertIn(
            "tests.test_planning_scope_discipline",
            ci_test_selection.dependencies("tools/prompts/fragments/goals/job-type-policy.md"),
        )

    def acceptance_stage_instructions(self):
        instructions = {}
        for stage in ("requirements_gather", "draft_plan", "revise_plan", "review_plan", "finalize_plan"):
            state = fixed_state()
            with (
                patch.object(catalog_context, "context", return_value=None),
                patch.object(planner, "workspace_inventory", return_value={"files": ["history.py"]}),
                patch.object(planner.artifacts, "verify_predecessor", return_value=None),
            ):
                prompt, _ = planner.context(state, stage, STATE_PATH)
            instructions[stage] = " ".join(prompt.split("\nCURRENT HANDOFF DATA\n", 1)[0].split())
        return instructions

    def test_ac1_acceptance_stages_render_discriminating_policy(self):
        phrases = (
            "DISCRIMINATING CRITERIA",
            "what the original code does with the same input",
            "a check the original code fails, including on negative paths",
            "unknown command, usage error, missing function, ImportError",
            "removed while everything else stays",
            "run as installed, isolated from the source tree",
            "default discovery observed as the default collection",
            "the input boundary the parser applies",
            "call existing APIs as they work on the current code",
            "take each worked-example operand from the record the rule applies to",
            "reconcile the public contract with any broader helper the approach reuses",
            "a distinguishing example",
            "keep the confirmation summary consistent with the requirement rows",
            "do not prescribe a per-item pass over a whole collection for a bulk operation when one pass serves,"
            " unless the request requires it",
        )
        instructions = self.acceptance_stage_instructions()
        self.assertEqual(5, len(instructions))
        for stage, instruction in instructions.items():
            for phrase in phrases:
                with self.subTest(stage=stage, phrase=phrase):
                    self.assertIn(phrase, instruction)

    def test_ac2_plan_reviewer_blocks_criterion_a_baseline_passes(self):
        blocking = "raise a blocking concern naming the baseline or broken variant that passes it"
        operand = "taking each operand from the record the rule applies to"
        instructions = self.acceptance_stage_instructions()
        for stage, instruction in instructions.items():
            with self.subTest(stage=stage):
                if stage in ("review_plan", "finalize_plan"):
                    self.assertIn(blocking, instruction)
                    self.assertIn(operand, instruction)
                else:
                    self.assertNotIn(blocking, instruction)
                    self.assertNotIn(operand, instruction)
        self.assertIn(operand, " ".join(planner.EXAMPLE_CHECK_RULE.split()))

    def test_every_stage_that_asks_the_person_frames_questions_around_the_requested_behavior(self):
        """2026-11-24 live run: asked about "an order not yet delivered, or after the 30-day window",
        Requirements offered only "raise an error" or "return a rejection", though the request's
        intended answer was that an undelivered order has no window. Every stage that may ask the
        person reads one framing rule."""
        phrases = (
            "QUESTION FRAMING",
            "options that together include the behavior the request and the saved answers ask for",
            "never bundle two cases that could get different answers",
            "show it on one concrete input, so the person chooses between outcomes",
            "Ask every question you need in one report, not one round at a time",
        )
        for stage, instruction in self.acceptance_stage_instructions().items():
            for phrase in phrases:
                with self.subTest(stage=stage, phrase=phrase):
                    if stage == "review_plan":
                        self.assertNotIn(phrase, instruction)
                    else:
                        self.assertIn(phrase, instruction)

    def test_external_data_shapes_are_asked_not_guessed_into_the_plan(self):
        """2026-11-24 live run, "kibana should send a message/alert to a service which should analyze the logs
        and post to slack" in an empty repository: all 17 criteria were checked against mocks of Elasticsearch
        fields (service.name, log.level, error.stack_trace) and a Kibana body the Planner invented, and nobody
        was asked. An adaptive-engine plan left a real decision as "Flagged for the user to confirm at
        approval" inside its technical approach. Every stage that writes or checks criteria reads one rule;
        the Plan Reviewer blocks on it."""
        rule = (
            "EXTERNAL CONTRACTS",
            "the shape is the person's fact, not an engineering choice, even in an empty repository",
            "offering to take a pasted sample",
            "never writes a guessed field name, payload or response into the technical approach or a criterion",
            "proves agreement with the guess, not with the person's system",
            "never left as a note in the technical approach",
        )
        review = "raise a blocking concern when a criterion or the technical approach rests on the shape"
        for stage, instruction in self.acceptance_stage_instructions().items():
            for phrase in rule:
                with self.subTest(stage=stage, phrase=phrase):
                    self.assertIn(phrase, instruction)
            with self.subTest(stage=stage, review=True):
                if stage in ("review_plan", "finalize_plan"):
                    self.assertIn(review, instruction)
                else:
                    self.assertNotIn(review, instruction)

    def test_ac3_acceptance_policy_selects_rendered_prompt_regression(self):
        self.assertIn(
            "tests.test_planning_scope_discipline",
            ci_test_selection.dependencies("tools/autocode_acceptance_policy.py"),
        )

    def test_blocking_questions_require_a_connection_to_the_requested_defect(self):
        for stage in planner.PROMPTS:
            for adaptive in (False, True):
                with self.subTest(stage=stage, adaptive=adaptive):
                    state = fixed_state()
                    state["task"] = "A single-record lookup shows result code 0. Fix the history entry."
                    state["settings"]["adaptive_planning"] = adaptive
                    before = copy.deepcopy(state)
                    with (
                        patch.object(catalog_context, "context", return_value=None),
                        patch.object(planner, "workspace_inventory", return_value={"files": ["history.py"]}),
                        patch.object(planner.artifacts, "verify_predecessor", return_value=None),
                    ):
                        prompt, _ = planner.context(state, stage, STATE_PATH)
                    instruction, raw = prompt.split("\nCURRENT HANDOFF DATA\n", 1)
                    instruction = " ".join(instruction.split())
                    for rule in (
                        "Before adding a blocking question",
                        "connect the decision to the requested behavior",
                        "A nearby capability is not evidence",
                        "preserve its existing behavior",
                        "explicit user requirement",
                        "Missing incident evidence remains an uncertainty",
                    ):
                        self.assertTrue(rule in instruction, f"{stage} lacks {rule!r}")
                    self.assertEqual(before, state)
                    self.assertIn(before["task"], json.dumps(json.loads(raw)))


if __name__ == "__main__":
    unittest.main()
