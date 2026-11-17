"""A saved rejected report is revalidated under the route that produced it, not the runner's later climb.

Board #68: after a repaired Tester report was rejected, the runner climbed the Tester's route
(xhigh -> max). The climb changed the saved revalidation context, so a fixed validator could no
longer re-check the saved report and a resume would have paid for a fresh max Tester attempt.
"""

from __future__ import annotations

import copy
import unittest

import autocode_report_revalidation_binding as binding

ROUTE = {"engine": "opencode", "model": "openai/gpt-6-sol", "provider": None, "reasoning_effort": "xhigh"}


def state() -> dict:
    return {
        "task": "Build chubby",
        "task_id": "task-1",
        "current_task": {"id": "t-6", "milestone_id": "M3"},
        "goal_contract": {"hash": "c1"},
        "settings": {"roles": {"tester": copy.deepcopy(ROUTE), "builder": {"model": "b", "reasoning_effort": "high"}}},
    }


ORIGINAL = {"stage": "tester", "role": "tester", "launch_route": copy.deepcopy(ROUTE)}


class RevalidationRouteTests(unittest.TestCase):
    def test_the_runners_route_climb_after_rejection_keeps_the_saved_binding(self):
        current = state()
        saved = binding.context(current, "tester")
        current["settings"]["roles"]["tester"]["reasoning_effort"] = "max"
        self.assertNotEqual(saved, binding.context(current, "tester"))
        self.assertTrue(binding.matches(current, "tester", saved, ORIGINAL))
        self.assertEqual("max", current["settings"]["roles"]["tester"]["reasoning_effort"], "state is not changed")

    def test_any_other_change_still_breaks_the_binding(self):
        saved = binding.context(state(), "tester")
        for change in (
            lambda s: s.update(task="Build something else"),
            lambda s: s["current_task"].update(milestone_id="M4"),
            lambda s: s["settings"]["roles"]["builder"].update(reasoning_effort="max"),
        ):
            current = state()
            current["settings"]["roles"]["tester"]["reasoning_effort"] = "max"
            change(current)
            with self.subTest(current=current):
                self.assertFalse(binding.matches(current, "tester", saved, ORIGINAL))

    def test_without_a_recorded_route_only_an_exact_binding_matches(self):
        current = state()
        saved = binding.context(current, "tester")
        self.assertTrue(binding.matches(current, "tester", saved, {"stage": "tester"}))
        current["settings"]["roles"]["tester"]["reasoning_effort"] = "max"
        for original in ({"stage": "tester"}, {**ORIGINAL, "role": "planner"}, {**ORIGINAL, "launch_route": None}):
            with self.subTest(original=original):
                self.assertFalse(binding.matches(current, "tester", saved, original))
        self.assertFalse(binding.matches(current, "tester", None, ORIGINAL))


if __name__ == "__main__":
    unittest.main()
