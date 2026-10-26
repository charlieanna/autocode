"""A request whose work spans another repository stops for the person before any plan (#2066, step 3).

Drives the real, offline CLI through the task-run interface only.
"""

import json
import unittest

import autocode_taskrun as taskrun

from .test_agent_step import fixture_workspace
from .test_taskrun import BRIEF, FIXTURE_OPTIONS

ELSEWHERE = "Also record each greeting in the greet-history service."
PART = f"greet-history|Record each greeting|{ELSEWHERE}"


class ScopeQuestionCliTests(unittest.TestCase):
    def start(self):
        workspace, env = fixture_workspace(self)
        env["AUTOCODE_FIXTURE_EXTERNAL_PART"] = PART
        self.run = taskrun.TaskRun.start(
            workspace,
            f"{BRIEF} {ELSEWHERE}",
            options=(*FIXTURE_OPTIONS, "--joint-planning", "--no-adaptive-planning"),
            env=env,
            timeout=180,
        )
        return self.run.advance_until_input()

    def stages(self):
        state = json.loads((self.run.run_dir / "state.json").read_text())
        return [row.get("stage") for row in state.get("stages", [])]

    def test_the_person_is_asked_before_any_plan_and_the_plan_records_what_is_left_out(self):
        view = self.start()
        self.assertEqual("answer", view["needs"]["kind"], (view["status"], view["stop_reason"]))
        [question] = view["needs"]["questions"]
        self.assertEqual("workspace-scope-1", question["id"])
        self.assertIn("greet-history: Record each greeting", question["question"])
        self.assertEqual(
            {
                "spans_repositories": True,
                "external_parts": [{"target": "greet-history", "work": "Record each greeting"}],
                "uncovered": [],
                "decision": None,
                "dropped": [],
                "linked": [],
            },
            view["scope"],
        )
        self.assertNotIn("astra_discovery", self.stages(), "no plan before the person decides")

        self.run.answer("workspace-scope-1", "This repository only")
        view = self.run.advance_until_input()
        self.assertEqual("AWAITING_GOAL_APPROVAL", view["status"], view["stop_reason"])
        self.assertEqual(["greet-history"], view["scope"]["uncovered"])
        self.assertEqual("this_repository", view["scope"]["decision"])
        self.assertIn(
            "Not covered: changes in greet-history: Record each greeting",
            json.dumps(view["displayed_plan"]),
            "the approval screen names what this run leaves out",
        )

    def test_a_claim_the_request_does_not_state_never_reaches_the_person(self):
        workspace, env = fixture_workspace(self)
        env["AUTOCODE_FIXTURE_EXTERNAL_PART"] = "billing|Charge for greetings|Bill every greeting in billing."
        self.run = taskrun.TaskRun.start(
            workspace,
            BRIEF,
            options=(*FIXTURE_OPTIONS, "--joint-planning", "--no-adaptive-planning"),
            env=env,
            timeout=180,
        )
        view = self.run.advance_until_input()
        self.assertEqual("AWAITING_GOAL_APPROVAL", view["status"], view["stop_reason"])
        self.assertFalse(view["scope"]["spans_repositories"])
        self.assertEqual(["billing"], view["scope"]["dropped"])
        excluded = view["displayed_plan"]["scope_exclusions"] or []
        self.assertFalse([row for row in excluded if row.startswith("Not covered:")])


if __name__ == "__main__":
    unittest.main()
