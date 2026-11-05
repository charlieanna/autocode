"""Planning's budget snapshot preserves limits without inventing new authority."""

import unittest

import autocode_planning_time as planning_time


class PlanningTimeTests(unittest.TestCase):
    def test_missing_and_disabled_limits_stay_unbounded(self):
        for state in ({}, {"settings": {"limits": {"max_seconds": 0, "stage_timeout_seconds": 0, "max_cost_usd": 0}}}):
            with self.subTest(state=state):
                self.assertEqual(
                    {
                        "run_active_seconds_used": 0,
                        "run_active_seconds_remaining": None,
                        "stage_seconds_limit": None,
                        "run_cost_limit_usd": None,
                    },
                    planning_time.context(state),
                )

    def test_exhausted_time_is_zero_without_resetting_usage(self):
        self.assertEqual(
            {
                "run_active_seconds_used": 1810.5,
                "run_active_seconds_remaining": 0,
                "stage_seconds_limit": 600,
                "run_cost_limit_usd": 2,
            },
            planning_time.context(
                {
                    "active_seconds": 1810.5,
                    "settings": {"limits": {"max_seconds": 1800, "stage_timeout_seconds": 600, "max_cost_usd": 2}},
                }
            ),
        )
