"""The status view's ``pipeline``: the six pipeline jobs, where each stands, its route and its spend.

A display copy of records the view already holds (autocode_stage_pipeline), so a client can show the whole
pipeline without a table of job names of its own. These tests pin how each saved record reads.
"""

import unittest

import autocode_run_view as run_view
import autocode_stage_pipeline as stage_pipeline

ROUTES = {
    "requirements": {"model": "claude-sonnet-5-5", "reasoning_effort": "medium"},
    "planner": {"model": "claude-opus-5-5", "reasoning_effort": "high"},
    "plan_reviewer": {"model": "gpt-6", "reasoning_effort": "high"},
    "builder": {"model": "claude-opus-5-5", "reasoning_effort": "medium"},
    "tester": {"model": "glm-5.3", "reasoning_effort": " "},
}


def decision(stage, job, kind="run", **extra):
    return {"stage": stage, "job": job, "decision": kind, "reason": None, "effort_choice": None, **extra}


def view(**fields):
    return {"done": False, "routes": ROUTES, "stage_decisions": [], "run_metrics": {}, **fields}


def states(rows):
    return [(row["job"], row["state"]) for row in rows]


class PipelineTests(unittest.TestCase):
    def test_every_pipeline_job_is_one_row_in_order_with_its_route(self):
        rows = stage_pipeline.project({}, view())
        self.assertEqual(
            ["Requirements", "Planner", "Plan Reviewer", "Builder", "Tester", "Completion Reviewer"],
            [row["label"] for row in rows],
        )
        self.assertEqual({"pending"}, {row["state"] for row in rows})
        self.assertEqual(
            {
                "job": "planner",
                "label": "Planner",
                "state": "pending",
                "model": "claude-opus-5-5",
                "reasoning_effort": "high",
                "spent_usd": None,
                "reason": None,
            },
            rows[1],
        )
        tester, completion = rows[4], rows[5]
        self.assertIsNone(tester["reasoning_effort"], "a blank level is not a level")
        self.assertIsNone(completion["model"], "no route for the job: no model is invented")

    def test_the_running_stage_is_working_and_what_launched_before_it_ran(self):
        decisions = [
            decision("requirements_gather", "requirements"),
            decision("draft_plan", "planner"),
            decision("review_plan", "plan_reviewer"),
            decision("revise_plan", "planner", "skip", reason="approved at the first review"),
            decision("builder", "builder"),
        ]
        state = {"active_stage": {"stage": "builder"}, "next_stage": "tester"}
        rows = stage_pipeline.project(state, view(stage_decisions=decisions))
        self.assertEqual(
            [
                ("requirements", "ran"),
                ("planner", "ran"),
                ("plan_reviewer", "ran"),
                ("builder", "working"),
                ("tester", "pending"),
                ("completion", "pending"),
            ],
            states(rows),
        )
        self.assertIsNone(rows[1]["reason"], "a job that launched is never shown as skipped")

    def test_between_stages_or_waiting_the_next_stage_is_next(self):
        decisions = [decision("requirements_gather", "requirements"), decision("draft_plan", "planner")]
        rows = stage_pipeline.project({"next_stage": "review_plan"}, view(stage_decisions=decisions))
        self.assertEqual("next", rows[2]["state"])
        self.assertEqual(["ran", "ran"], [row["state"] for row in rows[:2]])

    def test_a_skipped_job_says_why_and_an_ended_run_marks_what_it_never_reached(self):
        decisions = [
            decision("requirements_gather", "requirements"),
            decision("draft_plan", "planner", "skip", reason="Requirements answered the discussion itself"),
            decision("builder", "builder", "skip", reason=""),
        ]
        state = {"active_stage": {"stage": "builder"}, "next_stage": "tester"}  # stale records of an ended run
        rows = stage_pipeline.project(state, view(done=True, stage_decisions=decisions))
        self.assertEqual(
            [
                ("requirements", "ran"),
                ("planner", "skipped"),
                ("plan_reviewer", "not_run"),
                ("builder", "skipped"),
                ("tester", "not_run"),
                ("completion", "not_run"),
            ],
            states(rows),
        )
        self.assertEqual("Requirements answered the discussion itself", rows[1]["reason"])
        self.assertIsNone(rows[3]["reason"], "an empty reason is not a reason")

    def test_the_level_a_rule_moved_and_the_spend_come_from_the_core_records(self):
        decisions = [
            decision("review_plan", "plan_reviewer", effort_choice={"level": "medium", "lowered": True}),
            decision("builder", "builder", effort_choice={"lowered": True}),
        ]
        metrics = {
            "by_job": [
                {"job": "Plan Reviewer", "spent_usd": 0.42},
                {"job": "Builder", "spent_usd": 1.5},
                {"job": "Tester", "spent_usd": -1},
            ]
        }
        rows = stage_pipeline.project({}, view(stage_decisions=decisions, run_metrics=metrics))
        self.assertEqual(("medium", 0.42), (rows[2]["reasoning_effort"], rows[2]["spent_usd"]))
        self.assertEqual(("medium", 1.5), (rows[3]["reasoning_effort"], rows[3]["spent_usd"]))
        self.assertIsNone(rows[4]["spent_usd"], "a negative amount is not spend")

    def test_malformed_records_show_the_plain_pipeline(self):
        rows = stage_pipeline.project(
            {"active_stage": ["corrupt"], "next_stage": 7},
            {"routes": "corrupt", "stage_decisions": [None, {"job": 3}], "run_metrics": {"by_job": "x"}},
        )
        self.assertEqual({"pending"}, {row["state"] for row in rows})
        self.assertEqual({None}, {row["model"] for row in rows})


class StatusViewTests(unittest.TestCase):
    def test_the_status_view_carries_the_pipeline_from_its_saved_stages(self):
        state = {
            "status": "RUNNING",
            "stages": [{"stage": "requirements_gather", "iteration": 1}, {"stage": "draft_plan", "iteration": 1}],
            "active_stage": {"stage": "review_plan", "iteration": 1},
        }
        pipeline = run_view.view(state)["pipeline"]
        self.assertEqual(
            [
                ("requirements", "ran"),
                ("planner", "ran"),
                ("plan_reviewer", "working"),
                ("builder", "pending"),
                ("tester", "pending"),
                ("completion", "pending"),
            ],
            states(pipeline),
        )


if __name__ == "__main__":
    unittest.main()
