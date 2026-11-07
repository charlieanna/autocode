"""The inactivity limit a stage runs under: the runner default, a model family's floor, or the user's (#298)."""

import unittest

import autocode_idle_policy as idle_policy

MIMO = "xiaomi-token-plan-sgp/mimo-v2.6-pro"


class IdlePolicyTests(unittest.TestCase):
    def test_a_long_silence_route_gets_its_floor_only_over_the_runner_default(self):
        self.assertEqual((900, "runner default for MiMo routes"), idle_policy.effective(300, "runner_default", MIMO))
        self.assertEqual(
            (900, "runner default for MiMo routes"),
            idle_policy.effective(300, "runner_default", "mimo-token-plan/mimo-v2.6-pro"),
        )

    def test_an_explicit_or_higher_limit_and_other_models_are_used_as_given(self):
        for limit, origin, model in (
            (300, "user_explicit", MIMO),  # the user's choice wins, even lower
            (300, "resolver_delegated", MIMO),  # may hold an explicit value
            (1200, "runner_default", MIMO),  # already above the floor
            (0, "runner_default", MIMO),  # inactivity limit switched off
            (300, None, MIMO),  # no saved origin: leave saved runs alone
            (300, "runner_default", "zai-coding-plan/glm-5.3"),
            (300, "runner_default", "openai/gpt-6-sol"),
            (300, "runner_default", None),
        ):
            with self.subTest(limit=limit, origin=origin, model=model):
                self.assertEqual((limit, origin), idle_policy.effective(limit, origin, model))

    def test_report_only_planning_stages_get_their_floor_over_the_runner_default(self):
        # 2026-10-20 trial: every GLM 5.3 Planner call took 300-340 s; three were killed at 308-310 s.
        glm = "zai-coding-plan/glm-5.3"
        for stage in ("requirements_gather", "astra_discovery", "astra_challenge", "glm_revise", "astra_finalize"):
            with self.subTest(stage=stage):
                self.assertEqual(
                    (600, idle_policy.PLANNING_FLOOR_ORIGIN),
                    idle_policy.effective(300, "runner_default", glm, stage=stage),
                )
        for stage in ("requirements", "plan", "plan_review", "plan_revise", "plan_finalize"):
            with self.subTest(stage=stage):
                self.assertEqual(
                    (600, idle_policy.PLANNING_FLOOR_ORIGIN),
                    idle_policy.effective(300, "runner_default", glm, stage=stage),
                )
        self.assertIn("Planner", idle_policy.PLANNING_FLOOR_ORIGIN, "the stop text names whose floor it was")

    def test_the_planning_floor_leaves_every_other_launch_and_limit_alone(self):
        glm = "zai-coding-plan/glm-5.3"
        for stage in ("terra", "sol", "astra_review", "astra_checkpoint", "review_change", "investigate_bug", None):
            with self.subTest(stage=stage):
                self.assertEqual(
                    (300, "runner_default"), idle_policy.effective(300, "runner_default", glm, stage=stage)
                )
        for limit, origin in (
            (300, "user_explicit"),
            (300, "resolver_delegated"),
            (1200, "runner_default"),
            (0, "runner_default"),
            (300, None),
        ):
            with self.subTest(limit=limit, origin=origin):
                self.assertEqual((limit, origin), idle_policy.effective(limit, origin, glm, stage="astra_discovery"))
        # A MiMo Planner gets the higher of the two floors, under MiMo's label.
        self.assertEqual(
            (900, "runner default for MiMo routes"),
            idle_policy.effective(300, "runner_default", MIMO, stage="astra_discovery"),
        )


if __name__ == "__main__":
    unittest.main()
