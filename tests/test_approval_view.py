"""The limits line shown at the plan approval stop (issue #381)."""
import unittest

import autocode_approval_view as approval_view


class LimitsTests(unittest.TestCase):
    def test_absent_or_zero_limits_read_as_no_limit(self):
        settings = {"limits": {"max_seconds": 0, "stage_timeout_seconds": None, "iteration_ceiling": None},
                    "orchestration": {"enabled": False, "max_parallel": 4}}
        self.assertEqual("Limits in effect: no time limit for the run, no per-stage time limit, "
                         "no iteration ceiling, one Builder at a time.", approval_view.limits(settings, 3))
        self.assertEqual(approval_view.limits(settings, 3), approval_view.limits({}, 3))

    def test_set_limits_read_in_hours_minutes_or_seconds(self):
        settings = {"limits": {"max_seconds": 5400, "stage_timeout_seconds": 45, "iteration_ceiling": 0},
                    "orchestration": {"enabled": True, "max_parallel": 3}}
        self.assertEqual("Limits in effect: 90 min of active time for the run, 45 s per stage, "
                         "stops after iteration 0 (now at 0), up to 3 Builders at once.",
                         approval_view.limits(settings, 0))


if __name__ == "__main__":
    unittest.main()
