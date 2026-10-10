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


if __name__ == "__main__":
    unittest.main()
