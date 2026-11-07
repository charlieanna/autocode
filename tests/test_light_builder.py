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
        "settings": {"effort_origins": {"terra": origin} if origin else {}},
        "goal_contract": {
            "approval_status": "approved" if approved else "draft",
            "body": {"milestones": [{"id": f"M{n}"} for n in range(milestones)]},
        },
        "current_task": {"id": "T2", "affected_paths": list(paths)},
        "stages": list(stages),
    }


class LightBuilderTests(unittest.TestCase):
    def test_the_first_builder_attempt_at_a_small_approved_task_runs_low(self):
        self.assertEqual("low", light.effort(state(), "terra", False, "medium"))
        self.assertEqual("low", light.effort(state(), "terra", False, "high"))

    def test_any_earlier_attempt_at_the_task_gets_the_configured_effort_back(self):
        for earlier in (
            {"stage": "terra", "task_id": "T2", "rejected": True},  # a rejected report
            {"stage": "terra", "task_id": "T2"},  # a build the Tester sent back
            {"stage": "terra_report_repair", "original_stage": "terra", "task_id": "T2"},
        ):
            with self.subTest(earlier=earlier):
                self.assertEqual("medium", light.effort(state(stages=[earlier]), "terra", False, "medium"))
        other_task = {"stage": "terra", "task_id": "T1"}
        self.assertEqual("low", light.effort(state(stages=[other_task]), "terra", False, "medium"))

    def test_bigger_or_unapproved_work_keeps_its_effort(self):
        for value in (
            state(milestones=2),
            state(paths=("a.py", "b.py", "c.py", "d.py", "e.py")),
            state(paths=()),
            state(approved=False),
        ):
            with self.subTest(value=value):
                self.assertEqual("medium", light.effort(value, "terra", False, "medium"))

    def test_other_stages_report_repairs_and_lower_settings_are_untouched(self):
        for stage in ("sol", "astra_review", "requirements_gather", "astra_plan", "astra_challenge"):
            with self.subTest(stage=stage):
                self.assertEqual("medium", light.effort(state(), stage, False, "medium"))
        self.assertEqual("medium", light.effort(state(), "terra", True, "medium"))
        self.assertEqual("low", light.effort(state(), "terra", False, "low"))
        self.assertIsNone(light.effort(state(), "terra", False, None))

    def test_an_effort_the_person_chose_or_nobody_recorded_is_never_lowered(self):
        # --terra-reasoning-effort high means every build runs at high; a saved run with no provenance keeps its effort.
        for origin in ("user_explicit", None):
            with self.subTest(origin=origin):
                self.assertEqual("high", light.effort(state(origin=origin), "terra", False, "high"))
        self.assertEqual("low", light.effort(state(origin="auto_selected"), "terra", False, "high"))

    def test_the_setting_turns_it_off(self):
        with mock.patch.dict(os.environ, {light.SETTING: "0"}):
            self.assertEqual("medium", light.effort(state(), "terra", False, "medium"))


if __name__ == "__main__":
    unittest.main()
