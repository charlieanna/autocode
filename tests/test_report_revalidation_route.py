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
    def test_launch_projection_recovers_only_the_hash_bound_original_null_or_absent_fields(self):
        for engine in ("absent", "null"):
            for provider in ("absent", "null"):
                with self.subTest(engine=engine, provider=provider):
                    original = state()
                    role = original["settings"]["roles"]["tester"]
                    role["custom_setting"] = "keep"
                    for key, form in (("engine", engine), ("provider", provider)):
                        if form == "absent":
                            role.pop(key, None)
                        else:
                            role[key] = None
                    saved = binding.context(original, "tester")
                    route = {key: role.get(key) for key in ("engine", "provider", "model", "reasoning_effort")}
                    record = {"stage": "tester", "role": "tester", "launch_route": route}
                    current = copy.deepcopy(original)
                    current["settings"]["roles"]["tester"]["reasoning_effort"] = "max"
                    self.assertTrue(binding.matches(current, "tester", saved, record))
                    candidates = list(binding._projected_roles(current["settings"]["roles"]["tester"], route))
                    matches = []
                    for candidate in candidates:
                        trial = copy.deepcopy(current)
                        trial["settings"]["roles"]["tester"] = candidate
                        if binding.context(trial, "tester") == saved:
                            matches.append(candidate)
                    self.assertEqual([role], matches)
                    for key, value in (("model", "other-model"), ("provider", "other-provider")):
                        altered = copy.deepcopy(record)
                        altered["launch_route"][key] = value
                        self.assertFalse(binding.matches(current, "tester", saved, altered))
                    changed = copy.deepcopy(current)
                    changed["settings"]["roles"]["tester"]["custom_setting"] = "changed"
                    self.assertFalse(binding.matches(changed, "tester", saved, record))

    def test_malformed_projection_cannot_invent_an_original_role(self):
        current = state()
        original = copy.deepcopy(current)
        original["settings"]["roles"]["tester"].pop("provider")
        saved = binding.context(original, "tester")
        current["settings"]["roles"]["tester"]["reasoning_effort"] = "max"
        for route in ({**ROUTE, "provider": None, "unknown": None}, {**ROUTE, "model": None}):
            with self.subTest(route=route):
                self.assertFalse(binding.matches(current, "tester", saved, {**ORIGINAL, "launch_route": route}))

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
