"""A small approved plan's first Builder attempt runs at low effort; anything else keeps its own (#1710)."""

import os
import unittest
from unittest import mock

import autocode_light_builder as light


def state(
    *,
    milestones=1,
    paths=("task_queue.py", "test_task_queue_regressions.py"),
    stages=(),
    approved=True,
    origin="runner_default",
):
    return {
        "settings": {"effort_origins": {"builder": origin} if origin else {}},
        "goal_contract": {
            "approval_status": "approved" if approved else "draft",
            "body": {"milestones": [{"id": f"M{n}"} for n in range(milestones)]},
        },
        "current_task": {"id": "T2", "affected_paths": list(paths)},
        "stages": list(stages),
    }


class LightBuilderTests(unittest.TestCase):
    def test_the_first_builder_attempt_at_a_small_approved_task_runs_low(self):
        self.assertEqual("low", light.effort(state(), "builder", False, "medium"))
        self.assertEqual("low", light.effort(state(), "builder", False, "high"))

    def test_any_earlier_attempt_at_the_task_gets_the_configured_effort_back(self):
        for earlier in (
            {"stage": "builder", "task_id": "T2", "rejected": True},  # a rejected report
            {"stage": "builder", "task_id": "T2"},  # a build the Tester sent back
            {"stage": "builder_report_repair", "original_stage": "builder", "task_id": "T2"},
        ):
            with self.subTest(earlier=earlier):
                self.assertEqual("medium", light.effort(state(stages=[earlier]), "builder", False, "medium"))
        other_task = {"stage": "builder", "task_id": "T1"}
        self.assertEqual("low", light.effort(state(stages=[other_task]), "builder", False, "medium"))

    def test_bigger_or_unapproved_work_keeps_its_effort_while_multi_milestone_leaf_tasks_still_step_down(self):
        self.assertEqual("low", light.effort(state(milestones=2), "builder", False, "medium"))
        for value in (
            state(paths=("a.py", "b.py", "c.py", "d.py", "e.py")),
            state(paths=()),
            state(approved=False),
        ):
            with self.subTest(value=value):
                self.assertEqual("medium", light.effort(value, "builder", False, "medium"))

    def test_stage_choice_builder_cascades_below_then_ceiling_then_above_and_raises_on_hard_tasks(self):
        glm = "zai-coding-plan/glm-5.3"
        base = state(milestones=3, origin="stage_choice")
        base["settings"]["roles"] = {"builder": {"model": glm, "reasoning_effort": "high"}}
        base["settings"]["declared_variants"] = {glm: ["low", "high", "max"]}
        base["settings"]["sensitive_paths"] = ["*auth*"]
        base["current_task"]["milestone_id"] = "M1"

        # 1) First bounded attempt steps down to highest offered level below high -> low.
        d1 = light.decide(base, "builder", "builder", False, "high", model=glm)
        self.assertEqual(("low", True, "bounded_task"), (d1["level"], d1["lowered"], d1["reason"]))

        # 2) Rework under a newly minted task_id for the same milestone M1 restores ceiling -> high,
        # even if the first attempt also needed a builder_report_repair.
        second = dict(
            base,
            task_archive=[{"id": "T2", "milestone_id": "M1"}],
            stages=[
                {"stage": "builder", "task_id": "T2"},
                {"stage": "builder_report_repair", "original_stage": "builder", "report_only": True, "task_id": "T2"},
            ],
        )
        second["current_task"] = {"id": "T3", "milestone_id": "M1", "affected_paths": ["task_queue.py"]}
        d2 = light.decide(second, "builder", "builder", False, "high", model=glm)
        self.assertEqual(("high", False, "earlier_attempt"), (d2["level"], d2["lowered"], d2["reason"]))

        # 3) Third attempt at M1 climbs one offered level above ceiling -> max.
        third = dict(
            second,
            task_archive=[{"id": "T2", "milestone_id": "M1"}, {"id": "T3", "milestone_id": "M1"}],
            stages=[{"stage": "builder", "task_id": "T2"}, {"stage": "builder", "task_id": "T3"}],
        )
        third["current_task"] = {"id": "T4", "milestone_id": "M1", "affected_paths": ["task_queue.py"]}
        d3 = light.decide(third, "builder", "builder", False, "high", model=glm)
        self.assertEqual(("max", True, "retry_climb"), (d3["level"], d3["raised"], d3["reason"]))

        # 4) Moving on to M2 starts fresh at low again.
        next_milestone = dict(third)
        next_milestone["current_task"] = {"id": "T5", "milestone_id": "M2", "affected_paths": ["other.py"]}
        d4 = light.decide(next_milestone, "builder", "builder", False, "high", model=glm)
        self.assertEqual(("low", True, "bounded_task"), (d4["level"], d4["lowered"], d4["reason"]))

        # 5) Sensitive-path or 6+-file task raises upfront to max.
        hard = dict(base)
        hard["current_task"] = {"id": "T6", "milestone_id": "M3", "affected_paths": ["src/auth_token.py"]}
        d5 = light.decide(hard, "builder", "builder", False, "high", model=glm)
        self.assertEqual(("max", True, "hard_task"), (d5["level"], d5["raised"], d5["reason"]))

    def test_other_stages_report_repairs_and_lower_settings_are_untouched(self):
        for stage in ("tester", "review_completion", "requirements_gather", "plan_task", "review_plan"):
            with self.subTest(stage=stage):
                self.assertEqual("medium", light.effort(state(), stage, False, "medium"))
        self.assertEqual("medium", light.effort(state(), "builder", True, "medium"))
        self.assertEqual("low", light.effort(state(), "builder", False, "low"))
        self.assertIsNone(light.effort(state(), "builder", False, None))

    def test_an_effort_the_person_chose_or_nobody_recorded_is_never_lowered(self):
        # --builder-reasoning-effort high means every build runs at high; a saved run with no provenance keeps its effort.
        for origin in ("user_explicit", None):
            with self.subTest(origin=origin):
                self.assertEqual("high", light.effort(state(origin=origin), "builder", False, "high"))
        self.assertEqual("low", light.effort(state(origin="auto_selected"), "builder", False, "high"))

    def test_parallel_worker_batches_restore_ceiling_after_integrated_attempt(self):
        import autocode_dispatch as dispatch

        glm = "zai-coding-plan/glm-5.3"
        parent = state(origin="stage_choice", milestones=3)
        parent["settings"]["roles"] = {"builder": {"model": glm, "reasoning_effort": "high"}}
        parent["settings"]["declared_variants"] = {glm: ["low", "high", "max"]}
        parent["orchestration_history"] = [
            {"id": "b1", "workers": [{"milestone_id": "M2", "status": "BUILT", "task": {"id": "w-m2"}}]}
        ]
        parent["stages"] = [{"stage": "builder", "task_id": "w-m2", "worker_milestone": "M2"}]

        # Direct rework on parent for combined batch [M2, M3] sees worker_milestone M2 -> restores high.
        parent["current_task"] = {"id": "rework-1", "milestone_ids": ["M2", "M3"], "affected_paths": ["m2.py"]}
        d_parent = light.decide(parent, "builder", "builder", False, "high", model=glm)
        self.assertEqual(
            ("high", False, "earlier_attempt"), (d_parent["level"], d_parent["lowered"], d_parent["reason"])
        )

        # Isolated parallel child worker (with stages=[]) inherits orchestration_history -> restores high for M2, keeps low for M4.
        child = dispatch.inherited_state(parent)
        child["stages"] = []
        child["current_task"] = {"id": "w-m2-retry", "milestone_id": "M2", "affected_paths": ["m2.py"]}
        d_child_retry = light.decide(child, "builder", "builder", False, "high", model=glm)
        self.assertEqual(
            ("high", False, "earlier_attempt"),
            (d_child_retry["level"], d_child_retry["lowered"], d_child_retry["reason"]),
        )

        child_fresh = dispatch.inherited_state(parent)
        child_fresh["stages"] = []
        child_fresh["current_task"] = {"id": "w-m4", "milestone_id": "M4", "affected_paths": ["m4.py"]}
        d_child_fresh = light.decide(child_fresh, "builder", "builder", False, "high", model=glm)
        self.assertEqual(
            ("low", True, "bounded_task"), (d_child_fresh["level"], d_child_fresh["lowered"], d_child_fresh["reason"])
        )

    def test_the_setting_turns_it_off(self):
        with mock.patch.dict(os.environ, {light.SETTING: "0"}):
            self.assertEqual("medium", light.effort(state(), "builder", False, "medium"))


if __name__ == "__main__":
    unittest.main()
