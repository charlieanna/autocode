"""Fitting a role's effort to the variants its model declares (#2006): pure functions."""

import unittest

import autocode_effort_variants as variants


class NearestTests(unittest.TestCase):
    def test_a_declared_effort_stays_and_an_undeclared_one_moves_to_the_next_stronger_else_weaker(self):
        glm = ["low", "high", "max"]  # GLM 5.3's declared variants: no medium, no xhigh
        self.assertEqual("high", variants.nearest("high", glm))
        self.assertEqual("high", variants.nearest("medium", glm))
        self.assertEqual("max", variants.nearest("xhigh", glm))
        self.assertEqual(
            "high", variants.nearest("xhigh", ["low", "medium", "high"]), "nothing stronger: the strongest weaker"
        )
        self.assertIsNone(variants.nearest("medium", []), "nothing declared: unknown, the launch check decides")
        self.assertEqual("low", variants.nearest("odd", glm))

    def test_the_flag_is_the_roles_own_or_the_global_one(self):
        self.assertEqual("--completion-reasoning-effort", variants.flag("completion"))
        self.assertEqual("--plan-reviewer-reasoning-effort", variants.flag("plan_reviewer"))
        self.assertEqual("--reasoning-effort", variants.flag("analyst"), "the Analyst has no flag of its own")


class ReconcileTests(unittest.TestCase):
    def roles(self):
        return {
            "terra": {"model": "m", "reasoning_effort": "high"},
            "completion": {"model": "m", "reasoning_effort": "medium"},
            "plan_reviewer": {"model": "m", "reasoning_effort": "xhigh"},
        }

    def test_runner_chosen_efforts_move_and_are_recorded_and_said(self):
        roles = self.roles()
        moved = variants.reconcile(roles, "zai-coding-plan/glm-5.3", ["low", "high", "max"])
        self.assertEqual(
            {"completion": {"from": "medium", "to": "high"}, "plan_reviewer": {"from": "xhigh", "to": "max"}}, moved
        )
        self.assertEqual(
            ("high", "high", "max"),
            tuple(roles[r]["reasoning_effort"] for r in ("terra", "completion", "plan_reviewer")),
        )
        note = variants.notice(moved, "zai-coding-plan/glm-5.3", ["low", "high", "max"])
        self.assertIn("declares the reasoning variants low, high, max", note)
        self.assertIn("completion medium -> high, plan_reviewer xhigh -> max", note)
        self.assertIsNone(variants.notice({}, "m", ["low"]))

    def test_an_effort_a_person_named_is_refused_with_its_flag_never_moved(self):
        roles = self.roles()
        with self.assertRaisesRegex(ValueError, "--completion-reasoning-effort.*declares low, high, max"):
            variants.reconcile(roles, "zai-coding-plan/glm-5.3", ["low", "high", "max"], explicit={"completion"})
        self.assertEqual("medium", roles["completion"]["reasoning_effort"], "nothing moved before the refusal")

    def test_nothing_declared_moves_nothing(self):
        roles = self.roles()
        self.assertEqual({}, variants.reconcile(roles, "m", None))
        self.assertEqual({}, variants.reconcile(roles, "m", []))
        self.assertEqual("medium", roles["completion"]["reasoning_effort"])

    def test_the_runs_recorded_candidate_levels_serve_a_route_answer(self):
        settings = {
            "route_selection": {
                "candidates": {
                    "sol": [{"model": "openai/gpt-6-sol", "levels": ["low", "medium", "high"]}],
                    "completion": [{"model": "zai-coding-plan/glm-5.3", "levels": ["low", "high", "max"]}],
                }
            }
        }
        self.assertEqual(["low", "high", "max"], variants.declared_for(settings, "zai-coding-plan/glm-5.3"))
        self.assertIsNone(variants.declared_for(settings, "xiaomi-token-plan-sgp/mimo-v2.6-pro"))
        self.assertIsNone(variants.declared_for({}, "m"))


if __name__ == "__main__":
    unittest.main()
