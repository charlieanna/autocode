"""Through the public CLI: feedback on a reviewed v2 plan starts a new planning cycle (#1252).

The v2 planning flow kept the previous cycle's plan-review charges after corrective feedback, so the
new plan's first review was refused as PAUSED_PLANNING_BUDGET (2/2 calls used) before any reviewer
read it. The legacy flow starts a fresh cycle for each new draft. Fake provider only; slow
(tests/suite_slow.json): the case drives the real CLI through two planning cycles.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

import autocode_taskrun as taskrun

SCENARIOS = Path(__file__).resolve().parents[1] / "scenarios"
if str(SCENARIOS) not in sys.path:
    sys.path.insert(0, str(SCENARIOS))
from harness import catalog
from harness.driver import fake_setup
from harness.project import materialize, without_maintenance


class V2FeedbackPlanningCycleCli(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="planning-cycle-cli-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.scenario = catalog.load("greenfield-greeting-cli")

    def start(self):
        project = without_maintenance(materialize(self.scenario.seed, self.root / "project"))
        flags, provider_env = fake_setup(self.scenario, self.root, self.scenario.dir / "reference")
        environment = {**provider_env, "AUTOCODE_HOME": str(self.root / "registry"), "PYTHONDONTWRITEBYTECODE": "1"}
        paths = environment["PATH"].split(os.pathsep)
        environment["PATH"] = os.pathsep.join([paths[0], str(Path(sys.executable).parent), *paths[1:]])
        options = (*flags, "--planning-v2", "--no-adaptive-planning")
        return taskrun.TaskRun.start(
            project, self.scenario.brief, options=options, env=environment, timeout=180, cwd=self.root
        )

    def reviewed_plan(self, run, view):
        """The plan awaiting approval: its review allowance line and the Plan Reviewer's saved reports."""
        self.assertEqual("approve_plan", (view.get("needs") or {}).get("kind"), (view["status"], view["stop_reason"]))
        goal = run.show_goal()
        allowance = re.search(r"Joint planning: (.+)", goal)
        reviews = re.findall(r"^  (plan_review|plan_finalize): (\S+)$", goal, re.MULTILINE)
        return (allowance[1] if allowance else None), dict(reviews)

    def test_feedback_on_a_reviewed_plan_gets_a_fresh_independent_review(self):
        run = self.start()
        allowance, first = self.reviewed_plan(run, run.advance_until_input())
        self.assertEqual("2/2 plan-review calls used", allowance)
        self.assertEqual({"plan_review", "plan_finalize"}, set(first))

        run.feedback("Print the greeting on its own line.")
        view = run.advance_until_input()

        # Before #1252 the first review of the corrected plan was refused for the old cycle's charges.
        self.assertNotEqual("planning_budget", (view.get("needs") or {}).get("kind"), view["stop_reason"])
        allowance, second = self.reviewed_plan(run, view)
        self.assertEqual("2/2 plan-review calls used", allowance)
        self.assertEqual({"plan_review", "plan_finalize"}, set(second))
        for stage in second:
            self.assertNotEqual(first[stage], second[stage], f"{stage} must review the corrected plan afresh")


if __name__ == "__main__":
    unittest.main()
