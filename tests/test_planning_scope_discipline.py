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
