"""A design job writes its design, not what an earlier turn of the conversation wrote (#664).

In a live discuss-then-design-then-build run the design turn's plan listed the decision record the
discuss turn had written (docs/decisions/metadata-cache.json) in its affected_paths, and its Builder
rewrote it. The runner now refuses such a plan where its author hands it in, unless the user's
newest message asks for that file. The approved-design rule also asks the Plan Reviewer to catch a
criterion that contradicts the approved design before approval, so the build never has to ask.
"""

import unittest

import autocode_goal_lifecycle as lifecycle
import autocode_workflows as workflows
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


class RewriteTests(unittest.TestCase):
    def test_a_design_plan_that_names_an_earlier_turns_file_is_found(self):
        self.assertEqual([(RECORD, RECORD)], workflows.design_rewrites(design_turn(), plan(DESIGN, RECORD)))
        self.assertEqual(
            [(RECORD, RECORD)], workflows.design_rewrites(design_turn(), plan(DESIGN, initial=(DESIGN, RECORD)))
        )

    def test_a_planned_folder_that_holds_it_counts(self):
        self.assertEqual([("docs/", RECORD)], workflows.design_rewrites(design_turn(), plan("docs/")))
        self.assertEqual([("docs/decisions", RECORD)], workflows.design_rewrites(design_turn(), plan("docs/decisions")))

    def test_its_own_design_folder_and_files_are_not_found(self):
        self.assertEqual([], workflows.design_rewrites(design_turn(), plan(DESIGN, "docs/design/")))
        # A sibling that only shares a prefix is no parent folder.
        self.assertEqual([], workflows.design_rewrites(design_turn(), plan("docs/decisions-archive/x.md")))

    def test_the_users_message_may_ask_for_the_file(self):
        for say in (f"Design it, and update {RECORD} to match.", "Design it and fix metadata-cache.json too."):
            with self.subTest(say=say):
                self.assertEqual([], workflows.design_rewrites(design_turn(say=say), plan(DESIGN, RECORD)))

    def test_only_a_design_job_proposing_a_design_is_checked(self):
        for state in (
            design_turn(kind="build"),
            design_turn(mode="review"),
            design_turn(wrote=()),
            {"workflow": {"kind": "design"}, "design_review": {"mode": "propose"}},
        ):
            with self.subTest(state=state):
                self.assertEqual([], workflows.design_rewrites(state, plan(DESIGN, RECORD)))


class DraftTests(unittest.TestCase):
    def test_the_design_plan_is_refused_where_its_author_hands_it_in(self):
        state = design_turn()
        with self.assertRaises(ValueError) as refused:
            lifecycle.install_draft(state, plan(DESIGN, RECORD), origin="plan")
        self.assertIn("does not change what an earlier turn of this conversation wrote", str(refused.exception))
        self.assertIn(f"affected_paths '{RECORD}' would let the Builder change {RECORD}", str(refused.exception))
        self.assertNotIn("goal_contract", state)
        lifecycle.install_draft(state, plan(DESIGN), origin="plan")
        self.assertEqual(1, state["goal_contract"]["revision"])

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
