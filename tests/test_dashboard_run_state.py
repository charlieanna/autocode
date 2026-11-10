"""The dashboard reads a run saved with job names under the old code names it still matches on."""

import json
import tempfile
import unittest
from pathlib import Path

from dashboard import dashboard_run_state as run_state


class LegacyNamesTests(unittest.TestCase):
    def test_routes_and_stages_saved_with_job_names_reach_the_dashboard_by_their_code_names(self):
        saved = {
            "next_stage": "review_completion",
            "settings": {"roles": {"planner": {"model": "a"}, "builder": {"model": "b"}, "completion": {}}},
            "sessions": {"task_planner": "s1", "tester": "s2"},
            "planning": {"reports": {"review_plan": {"report": {}}, "revise_plan": {}}},
            "goal": {"origin": "finalize_plan"},
            "stages": [
                {"stage": "draft_plan_report_repair", "role": "planner", "route_role": "task_planner"},
                {"stage": "builder", "role": "builder"},
                {"stage": "requirements_gather", "role": "requirements"},
            ],
            "active_stage": {"stage": "resolve", "role": "resolver"},
            "task": "a builder the tester checks",
        }
        with tempfile.TemporaryDirectory() as run:
            Path(run, "state.json").write_text(json.dumps(saved))
            state = run_state.read(run)
        self.assertEqual("astra_review", state["next_stage"])
        self.assertEqual({"glm", "terra", "completion"}, set(state["settings"]["roles"]))
        self.assertEqual({"astra": "s1", "sol": "s2"}, state["sessions"])
        self.assertEqual({"astra_challenge", "glm_revise"}, set(state["planning"]["reports"]))
        self.assertEqual("astra_finalize", state["goal"]["origin"])
        self.assertEqual(
            [
                ("astra_discovery_report_repair", "glm", "astra"),
                ("terra", "terra", None),
                ("requirements_gather", "requirements", None),
            ],
            [(row["stage"], row["role"], row.get("route_role")) for row in state["stages"]],
        )
        self.assertEqual(("astra_resolve", "resolver"), (state["active_stage"]["stage"], state["active_stage"]["role"]))
        self.assertEqual("a builder the tester checks", state["task"], "free text is never renamed")


if __name__ == "__main__":
    unittest.main()
