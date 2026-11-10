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
