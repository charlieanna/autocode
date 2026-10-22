"""A chat's Requirements names the job it understood, and the chat starts that job (#1846).

These CLI tests drive spec-first chat runs through the task-run interface with the scenario harness's
fake provider, whose Requirements names the job by the recognizer's keyword rules. Before #1846 a
question was dressed as a build: its handoff was refused for lacking acceptance tests, a paid report
repair invented answer-shaped ones, and the person was asked twice to approve preparing a plan that the
discuss workflow never produced. The status view, the stage artifacts and the workspace are the oracles.
"""

from __future__ import annotations

import os
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

CHAT = ("--joint-planning", "--no-adaptive-planning", "--spec-first")
REQUIREMENTS_REVIEW = "requirements-review-"


class RequirementsNamesTheJobCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="requirements-job-cli-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def start(self, scenario_id):
        scenario = catalog.load(scenario_id)
        project = without_maintenance(materialize(scenario.seed, self.root / "project"))
        flags, provider_env = fake_setup(scenario, self.root, scenario.dir / "reference")
        environment = {**provider_env, "AUTOCODE_HOME": str(self.root / "registry"), "PYTHONDONTWRITEBYTECODE": "1"}
        paths = environment["PATH"].split(os.pathsep)
        environment["PATH"] = os.pathsep.join([paths[0], str(Path(sys.executable).parent), *paths[1:]])
        run = taskrun.TaskRun.start(
            project, scenario.brief, options=(*flags, *CHAT), env=environment, timeout=240, cwd=self.root
        )
        return run, project

    def drive(self, run):
        """Serve every public gate as a cooperative person would; return the final view and what was offered."""
        offers = []
        for _ in range(12):
            view = run.advance_until_input()
            need = view.get("needs") or {}
            if view["done"] or need.get("kind") != "answer":
                return view, offers
            question = need["questions"][0]
            if question["id"].startswith(REQUIREMENTS_REVIEW):
                offers.append({"question": question["question"], "review": (view.get("chat") or {}).get("review")})
                answer = view["chat"]["review"]["answer"]
            else:
                answer = question.get("proposed_default") or "No preference"
            run.answer(question["id"], answer, resolver_token=need["resolver_token"])
        self.fail("The run did not finish within twelve public gates")

    @staticmethod
    def ran(run, slug):
        return len(list(Path(run.run_dir).glob(f"iterations/*/{slug}-[0-9][0-9].jsonl")))

    def details(self, view):
        return {key: view.get(key) for key in ("status", "next_stage", "needs", "stop_reason", "workflow")}

    def test_a_question_goes_from_requirements_straight_to_the_analyst(self):
        run, project = self.start("discuss-cache-choice")
        view, offers = self.drive(run)
        self.assertTrue(view["done"], self.details(view))
        self.assertEqual("discuss", view["workflow"], self.details(view))
        self.assertEqual([], offers, "Nothing is planned for a question, so nothing asks to prepare a plan")
        self.assertEqual(1, self.ran(run, "requirements-gather"))
        self.assertEqual(0, self.ran(run, "requirements-gather-report-repair"), "No repair invents acceptance tests")
        self.assertEqual(0, self.ran(run, "recognize-workflow"), "Requirements already named the job")
        self.assertEqual(1, self.ran(run, "analyst"))
        self.assertEqual(0, self.ran(run, "discovery"))
        self.assertIsNone(view.get("displayed_plan"))
        self.assertTrue((project / "docs/decisions/metadata-cache.json").is_file())

    def test_a_review_is_confirmed_as_a_review_and_starts_without_the_recognizer(self):
        run, _ = self.start("review-clean-pr")
        view, offers = self.drive(run)
        self.assertTrue(view["done"], self.details(view))
        self.assertEqual("review", view["workflow"], self.details(view))
        self.assertEqual(1, len(offers), offers)
        offer = offers[0]
        self.assertIn("start the review", offer["question"])
        self.assertNotIn("plan", offer["question"].rsplit("\n", 1)[-1])
        self.assertEqual("Approve understanding and start the review", offer["review"]["label"])
        self.assertNotIn("plan", offer["review"]["spend_confirmation"]["detail"])
        self.assertEqual(0, self.ran(run, "requirements-gather-report-repair"))
        self.assertEqual(0, self.ran(run, "recognize-workflow"), "Requirements already named the job")
        self.assertEqual(1, self.ran(run, "review-change"))
        self.assertEqual(0, self.ran(run, "discovery"))


if __name__ == "__main__":
    unittest.main()
