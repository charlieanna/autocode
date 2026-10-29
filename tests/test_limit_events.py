"""A person's limit change on resume is the run's own event, and never the last decision (#2412)."""

import unittest

import autocode_limit_events as limit_events


def settings(limits, origins=None):
    return {"limits": dict(limits), "budget_origins": dict(origins or {})}


class LimitEventTests(unittest.TestCase):
    def test_a_changed_explicit_limit_is_recorded_with_its_flag_and_both_values(self):
        state = {"next_stage": "terra", "user_events": [{"kind": "answer", "at": "t0"}]}
        previous = settings({"iteration_ceiling": 1, "max_seconds": 600}, {"iteration_ceiling": "user_explicit"})
        selected = settings(
            {"iteration_ceiling": 5, "max_seconds": 600, "max_cost_usd": 2.5},
            {"iteration_ceiling": "user_explicit", "max_cost_usd": "user_explicit"},
        )
        appended = limit_events.record(state, previous, selected, at="t1")
        self.assertEqual(
            [
                {
                    "kind": "limit_change",
                    "actor": "user_cli",
                    "at": "t1",
                    "limit": "iteration_ceiling",
                    "flag": "--max-iterations",
                    "previous": 1,
                    "value": 5,
                    "stage": "terra",
                },
                {
                    "kind": "limit_change",
                    "actor": "user_cli",
                    "at": "t1",
                    "limit": "max_cost_usd",
                    "flag": "--max-cost-usd",
                    "previous": None,
                    "value": 2.5,
                    "stage": "terra",
                },
            ],
            appended,
        )
        self.assertEqual([{"kind": "answer", "at": "t0"}, *appended], state["user_events"])

    def test_an_unchanged_or_runner_chosen_limit_records_nothing(self):
        state = {"next_stage": "terra"}
        previous = settings({"iteration_ceiling": 5, "max_seconds": 600}, {"iteration_ceiling": "user_explicit"})
        same = settings({"iteration_ceiling": 5, "max_seconds": 900}, {"iteration_ceiling": "user_explicit"})
        self.assertEqual([], limit_events.record(state, previous, same, at="t1"))
        self.assertNotIn("user_events", state)

    def test_removing_the_ceiling_is_recorded_under_its_own_flag(self):
        state = {"next_stage": "terra"}
        previous = settings({"iteration_ceiling": 5}, {"iteration_ceiling": "user_explicit"})
        unlimited = settings({"iteration_ceiling": None}, {"iteration_ceiling": "user_explicit"})
        [event] = limit_events.record(state, previous, unlimited, at="t1")
        self.assertEqual(("--unlimited-iterations", 5, None), (event["flag"], event["previous"], event["value"]))

    def test_the_last_decision_skips_limit_changes(self):
        approval = {"kind": "goal_approval", "at": "t0"}
        raise_ = {"kind": "limit_change", "at": "t1", "limit": "max_seconds"}
        self.assertEqual(approval, limit_events.last_decision({"user_events": [approval, raise_]}))
        self.assertEqual(approval, limit_events.last_decision({"user_events": [approval]}))
        self.assertIsNone(limit_events.last_decision({"user_events": [raise_]}))
        self.assertIsNone(limit_events.last_decision({}))


if __name__ == "__main__":
    unittest.main()
