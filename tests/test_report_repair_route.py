"""A retained report repair keeps the exact physical attempt's temporary route."""

import copy
import unittest

import autocode_report_repair_route as repair_route


class ReportRepairRouteTests(unittest.TestCase):
    def fixture(self):
        state = {"settings": {"roles": {"task_planner": {"model": "different-default"}}}}
        original = {
            "stage": "investigate_stuck",
            "route_role": "stuck_investigator",
            "launch_route": {
                "model": "independent-model",
                "engine": "opencode",
                "provider": "external-tool",
                "reasoning_effort": "high",
            },
        }
        return state, original

    def test_restore_keeps_original_engine_model_provider_effort_without_aliasing(self):
        state, original = self.fixture()
        before = copy.deepcopy(original)
        repair_route.restore(state, original)
        self.assertEqual(original["launch_route"], state["settings"]["roles"]["stuck_investigator"])
        self.assertEqual({"model": "different-default"}, state["settings"]["roles"]["task_planner"])
        state["settings"]["roles"]["stuck_investigator"]["model"] = "mutation"
        self.assertEqual(before, original)

    def test_existing_matching_route_is_unchanged_and_conflicts_refuse(self):
        for field in (None, "model", "engine", "provider", "reasoning_effort"):
            with self.subTest(field=field):
                state, original = self.fixture()
                state["settings"]["roles"]["stuck_investigator"] = copy.deepcopy(original["launch_route"])
                if field:
                    state["settings"]["roles"]["stuck_investigator"][field] = "changed"
                before = copy.deepcopy(state)
                if field:
                    with self.assertRaisesRegex(repair_route.util.Paused, "route changed"):
                        repair_route.restore(state, original)
                else:
                    repair_route.restore(state, original)
                self.assertEqual(before, state)

    def test_inherited_engine_is_restored_from_the_attempt_not_the_current_default(self):
        for existing in (False, True):
            with self.subTest(existing=existing):
                state, original = self.fixture()
                original["engine"] = "codex"
                original["launch_route"]["engine"] = None
                state["settings"]["engine"] = "codex" if existing else "opencode"
                if existing:
                    route = copy.deepcopy(original["launch_route"])
                    del route["engine"]
                    state["settings"]["roles"]["stuck_investigator"] = route
                repair_route.restore(state, original)
                restored = state["settings"]["roles"]["stuck_investigator"]
                self.assertEqual("codex", restored.get("engine", state["settings"]["engine"]))
                self.assertEqual("independent-model", restored["model"])
                self.assertIsNone(original["launch_route"]["engine"])

    def test_unrecorded_inherited_engine_refuses_instead_of_selecting_a_new_one(self):
        state, original = self.fixture()
        original["launch_route"]["engine"] = None
        before = copy.deepcopy(state)
        with self.assertRaisesRegex(repair_route.util.Paused, "no original engine"):
            repair_route.restore(state, original)
        self.assertEqual(before, state)

    def test_missing_original_route_refuses_instead_of_using_default(self):
        for route in (None, {}, {"model": None}, {"model": ""}):
            with self.subTest(route=route):
                state, original = self.fixture()
                original["launch_route"] = route
                before = copy.deepcopy(state)
                with self.assertRaisesRegex(repair_route.util.Paused, "no original launch route"):
                    repair_route.restore(state, original)
                self.assertEqual(before, state)

    def test_other_stages_and_records_without_a_temporary_route_are_not_rewritten(self):
        for field, value in (("stage", "tester"), ("route_role", "task_planner"), ("route_role", None)):
            with self.subTest(field=field):
                state, original = self.fixture()
                original[field] = value
                before = copy.deepcopy(state)
                repair_route.restore(state, original)
                self.assertEqual(before, state)
