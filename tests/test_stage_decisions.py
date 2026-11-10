"""The status view's ``stage_decisions``: which stages a run ran and which it skipped, and why.

A copy of decisions the runner already saved (autocode_stage_decisions): these tests pin what each saved
decision reads as, and that a stage which launched is never reported as skipped.
"""

import unittest

import autocode_run_view as run_view
import autocode_stage_decisions as stage_decisions

LOWERED = {"level": "medium", "ceiling": "high", "lowered": True, "reason": "small_clear", "text": "small"}


def launched(*stages, **extra):
    return [{"stage": stage, "iteration": 1, **extra} for stage in stages]


def rows(state):
    return [(row["stage"], row["decision"]) for row in stage_decisions.view(state)]


class RunRowsTests(unittest.TestCase):
    def test_every_launched_stage_is_one_run_row_in_launch_order_with_its_count(self):
        state = {
            "stages": [
                *launched("requirements_gather", "astra_discovery", "astra_challenge", "terra"),
                {"stage": "regression_proof"},  # a runner check, not a stage with a job
                {"stage": "sol"},
                {"stage": "sol_report_repair", "report_only": True},  # a repair is not a launch of its own
                {"stage": "terra", "effort_choice": LOWERED},
                {"stage": "resolver"},  # the runner's own recovery step
            ],
            "active_stage": {"stage": "sol"},
        }
        shown = stage_decisions.view(state)
        self.assertEqual(
            ["requirements_gather", "astra_discovery", "astra_challenge", "terra", "sol"],
            [row["stage"] for row in shown],
        )
        self.assertEqual([1, 1, 1, 2, 2], [row["launches"] for row in shown])
        self.assertEqual({"run"}, {row["decision"] for row in shown})
        builder = shown[3]
        self.assertEqual(
            {
                "stage": "terra",
                "job": "builder",
                "label": "Builder",
                "decision": "run",
                "reason": None,
                "covered_by": [],
                "launches": 2,
                "effort_choice": LOWERED,
            },
            builder,
        )
        self.assertIsNone(shown[4]["effort_choice"], "the latest launch's level rule, not an earlier one")

    def test_a_run_with_no_launches_or_a_malformed_record_lists_nothing(self):
        self.assertEqual([], stage_decisions.view({}))
        self.assertEqual([], stage_decisions.view({"stages": "corrupt", "active_stage": [], "planning": []}))


class SkipRowsTests(unittest.TestCase):
    def test_an_early_approval_skips_the_revision_and_the_final_review_after_the_first_review(self):
        state = {
            "stages": launched("requirements_gather", "astra_discovery", "astra_challenge"),
            "planning": {"adaptive": {"approved_at": "astra_challenge#1", "challenges": 1}},
        }
        self.assertEqual(
            [
                ("requirements_gather", "run"),
                ("astra_discovery", "run"),
                ("astra_challenge", "run"),
                ("glm_revise", "skip"),
                ("astra_finalize", "skip"),
            ],
            rows(state),
        )
        revise, final = stage_decisions.view(state)[3:]
        self.assertEqual("Planner", revise["label"])
        self.assertEqual("no revision: the Plan Reviewer raised no blocking concern", revise["reason"])
        self.assertEqual("no final review: the Plan Reviewer approved the plan at its review 1", final["reason"])
        self.assertEqual(["astra_challenge", "person:approve_goal"], final["covered_by"])
        self.assertEqual(0, final["launches"])

    def test_an_approval_after_a_revision_skips_only_the_final_review(self):
        state = {
            "stages": launched(
                "requirements_gather", "astra_discovery", "astra_challenge", "glm_revise", "astra_challenge"
            ),
            "planning": {"adaptive": {"approved_at": "astra_challenge#2", "challenges": 2}},
        }
        self.assertEqual(
            [
                ("requirements_gather", "run"),
                ("astra_discovery", "run"),
                ("astra_challenge", "run"),
                ("glm_revise", "run"),
                ("astra_finalize", "skip"),
            ],
            rows(state),
        )
        self.assertIn("at its review 2", stage_decisions.view(state)[-1]["reason"])

    def test_without_an_early_approval_nothing_is_skipped(self):
        state = {
            "stages": launched("requirements_gather", "astra_discovery", "astra_challenge", "glm_revise"),
            "planning": {"adaptive": {"approved_at": None, "challenges": 1}},
        }
        self.assertNotIn("skip", {decision for _, decision in rows(state)})

    def test_a_discussion_requirements_answered_skips_planning_and_building(self):
        state = {
            "status": "TASK_COMPLETE",
            "stages": launched("requirements_gather"),
            "answer": {"answer": "Because ...", "answered_by": "requirements_gather"},
        }
        self.assertEqual(
            [
                ("requirements_gather", "run"),
                ("astra_discovery", "skip"),
                ("astra_challenge", "skip"),
                ("terra", "skip"),
                ("sol", "skip"),
                ("astra_review", "skip"),
            ],
            rows(state),
        )
        skipped = stage_decisions.view(state)[1]
        self.assertEqual(
            "Requirements answered the question from the code, and nothing was to be written", skipped["reason"]
        )
        self.assertEqual(["requirements_gather"], skipped["covered_by"])

    def test_an_answer_skips_nothing_until_the_run_finished_on_it_and_never_a_stage_that_launched(self):
        answer = {"answer": "Because ...", "answered_by": "requirements_gather"}
        # A follow-up turn that asked for a change is planning after the earlier turn's answer.
        working = {"status": "RUNNING", "stages": launched("requirements_gather", "astra_discovery"), "answer": answer}
        self.assertEqual([("requirements_gather", "run"), ("astra_discovery", "run")], rows(working))
        built = {
            "status": "TASK_COMPLETE",
            "stages": launched(
                "requirements_gather", "astra_discovery", "astra_challenge", "terra", "sol", "astra_review"
            ),
            "answer": answer,
        }
        self.assertNotIn("skip", {decision for _, decision in rows(built)})
        legacy = {**built, "stages": launched("answer_question"), "answer": {"answered_by": "answer_question"}}
        self.assertEqual([("answer_question", "run")], rows(legacy))

    def test_a_saved_compact_bug_fix_shows_the_omitted_planner_draft_with_its_coverage(self):
        covered = ["runner:compact_draft", "astra_challenge", "person:approve_goal", "runner:regression_proof"]
        state = {
            "stages": launched("requirements_gather", "investigate_bug", "astra_challenge"),
            "workflow": {
                "stage_policy": {
                    "version": 1,
                    "path": "compact",
                    "decisions": [
                        {
                            "stage": "astra_discovery",
                            "decision": "omitted",
                            "reason": "the runner drafted",
                            "covered_by": covered,
                        }
                    ],
                }
            },
        }
        self.assertEqual(
            [
                ("requirements_gather", "run"),
                ("investigate_bug", "run"),
                ("astra_discovery", "skip"),
                ("astra_challenge", "run"),
            ],
            rows(state),
        )
        self.assertEqual(covered, stage_decisions.view(state)[2]["covered_by"])
        included = {
            **state,
            "workflow": {"stage_policy": {"decisions": [{"stage": "astra_discovery", "decision": "included"}]}},
        }
        self.assertNotIn("astra_discovery", [stage for stage, _ in rows(included)])


class StatusViewTests(unittest.TestCase):
    def test_the_status_view_carries_the_record(self):
        state = {
            "status": "AWAITING_GOAL_APPROVAL",
            "stages": launched("requirements_gather", "astra_discovery", "astra_challenge"),
            "planning": {"adaptive": {"approved_at": "astra_challenge#1"}},
        }
        self.assertEqual(stage_decisions.view(state), run_view.view(state)["stage_decisions"])
        self.assertEqual([], run_view.view({"status": "RUNNING"})["stage_decisions"])


if __name__ == "__main__":
    unittest.main()
