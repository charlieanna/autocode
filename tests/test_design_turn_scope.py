"""A design job writes its design, not what an earlier turn of the conversation wrote (#664).

In a live discuss-then-design-then-build run the design turn's plan listed the decision record the
discuss turn had written (docs/decisions/metadata-cache.json) in its affected_paths, and its Builder
rewrote it. The Planner is told not to (the design-deliverables rule); the runner's own refusal served
the retired Designer stage and went with it. The approved-design rule also asks the Plan Reviewer to
catch a criterion that contradicts the approved design before approval, so the build never has to ask.
"""

import unittest

import autocode_goal_lifecycle as lifecycle
from goal_fixtures import body
from units import autoplanner

RECORD = "docs/decisions/metadata-cache.json"
DESIGN = "docs/design/metadata-cache.md"


def design_turn(say="Shared it is; design it.", wrote=(RECORD,), mode="propose", kind="design"):
    return {
        "task_id": "t",
        "answers": {},
        "user_events": [],
        "workflow": {"kind": kind},
        "design_review": {"mode": mode},
        "turns": [{"say": say, "previous": {"workflow": "discuss", "wrote": list(wrote)}}],
    }


def plan(*paths, initial=()):
    value = body()
    value["milestones"][0]["affected_paths"] = list(paths)
    if initial:
        value["initial_task"] = {
            "objective": "Write the design",
            "affected_paths": list(initial),
            "kind": "implement",
            "milestone_id": "M1",
            "requirements": ["Write it"],
            "acceptance_criteria": ["C1"],
            "validation_plan": ["python3 -m unittest -v"],
        }
    return value


class DraftTests(unittest.TestCase):
    def test_a_build_turn_may_still_change_what_the_design_turn_wrote(self):
        state = design_turn(kind="build", wrote=(DESIGN,))
        lifecycle.install_draft(state, plan("app/cache.py", DESIGN), origin="plan")
        self.assertEqual(1, state["goal_contract"]["revision"])


class PromptTests(unittest.TestCase):
    def test_the_planners_are_told_both_rules(self):
        self.assertIn("A file an earlier turn of this conversation wrote", autoplanner.DESIGN_DELIVERABLES_RULE)
        self.assertIn("The Plan Reviewer checks each one against approved_design", autoplanner.APPROVED_DESIGN_RULE)
        self.assertIn("blocking concern before approval", autoplanner.APPROVED_DESIGN_RULE)


if __name__ == "__main__":
    unittest.main()
