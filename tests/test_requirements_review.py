"""Spec-first runs stop after Requirements until the user confirms (#1200).

The CLI tests drive a real run through the task-run interface with the scenario harness's fake
provider; the public status view and the provider's stage sequence are the oracles.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

import autocode_requirements_review as review
import autocode_taskrun as taskrun

SCENARIOS = Path(__file__).resolve().parents[1] / "scenarios"
if str(SCENARIOS) not in sys.path:
    sys.path.insert(0, str(SCENARIOS))
from harness import catalog
from harness.driver import fake_setup
from harness.project import materialize, without_maintenance

JOINT = ("--joint-planning", "--no-adaptive-planning")


class SettingTests(unittest.TestCase):
    def test_off_unless_asked_and_refused_where_requirements_gather_does_not_run(self):
        self.assertFalse(review.new_run_setting(None, True, False))
        self.assertFalse(review.new_run_setting(False, True, False))
        self.assertTrue(review.new_run_setting(True, True, False))
        for joint, v2 in ((False, False), (True, True)):
            with self.assertRaisesRegex(ValueError, "--spec-first needs joint planning"):
                review.new_run_setting(True, joint, v2)

    def test_a_resume_cannot_change_the_saved_flow(self):
        self.assertFalse(review.resume_refused({}, None))
        self.assertFalse(review.resume_refused({"spec_first": True}, True))
        self.assertTrue(review.resume_refused({}, True))
        self.assertTrue(review.resume_refused({"spec_first": True}, False))

    def test_the_question_shows_the_handoff_and_a_new_round_asks_again(self):
        report = {
            "intended_outcome": "Greet by name",
            "required_behaviors": ["prints Hello, NAME"],
            "acceptance_tests": ["greet.py Ada prints Hello, Ada"],
            "ignored_statements": ["colour output"],
            "open_questions": [],
        }
        state = {"settings": {"spec_first": True}, "requirements_handoff": {"report": report}}
        first = review.review_question(state)
        self.assertTrue(first["id"].startswith(review.PREFIX))
        self.assertEqual("plan", first["proposed_default"])
        for text in ("Greet by name", "prints Hello, NAME", "greet.py Ada prints Hello, Ada", "Left out:"):
            self.assertIn(text, first["question"])
        state["requirements_history"] = [{"report": report}]
        self.assertNotEqual(first["id"], review.review_question(state)["id"])
        self.assertEqual([], review.questions({**state, "settings": {}}))


class RequirementsReviewCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="requirements-review-cli-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.scenario = catalog.load("greenfield-greeting-cli")

    def start(self, *planning):
        project = without_maintenance(materialize(self.scenario.seed, self.root / "project"))
        flags, provider_env = fake_setup(self.scenario, self.root, self.scenario.dir / "reference")
        environment = {**provider_env, "AUTOCODE_HOME": str(self.root / "registry"), "PYTHONDONTWRITEBYTECODE": "1"}
        paths = environment["PATH"].split(os.pathsep)
        environment["PATH"] = os.pathsep.join([paths[0], str(Path(sys.executable).parent), *paths[1:]])
        run = taskrun.TaskRun.start(
            project, self.scenario.brief, options=(*flags, *planning), env=environment, timeout=180, cwd=self.root
        )
        return run, run.advance_until_input()

    @staticmethod
    def stages(run):
        """Stages that ran, from their retained artifacts (iterations/NNN/<slug>-NN.jsonl)."""
        slugs = {"requirements-gather": "requirements_gather", "discovery": "astra_discovery"}
        found = [
            path.stem.rsplit("-", 1)[0] for path in sorted(Path(run.run_dir).glob("iterations/*/*-[0-9][0-9].jsonl"))
        ]
        return [slugs.get(name, name) for name in found]

    def review_need(self, view):
        need = view.get("needs") or {}
        self.assertEqual("answer", need.get("kind"), {key: view.get(key) for key in ("status", "next_stage", "needs")})
        ids = [row["id"] for row in need["questions"]]
        self.assertTrue(ids[-1].startswith(review.PREFIX), ids)
        self.assertEqual("astra_discovery", view["next_stage"])
        return need, ids[-1]

    def test_without_the_setting_requirements_flow_straight_into_planning(self):
        run, view = self.start(*JOINT)
        self.assertEqual("approve_plan", (view.get("needs") or {}).get("kind"), view.get("needs"))

    def test_plan_releases_the_planner_after_the_user_sees_the_requirements(self):
        run, view = self.start(*JOINT, "--spec-first")
        need, question = self.review_need(view)
        self.assertIn("Here’s what I understand", need["questions"][-1]["question"])
        self.assertNotIn("astra_discovery", self.stages(run))
        view = run.answer(question, "plan", resolver_token=need["resolver_token"])
        view = run.advance_until_input()
        self.assertEqual("approve_plan", (view.get("needs") or {}).get("kind"), view.get("needs"))
        stages = self.stages(run)
        self.assertEqual(1, stages.count("requirements_gather"), stages)
        self.assertIn("astra_discovery", stages)

    def test_a_correction_runs_requirements_again_and_asks_again(self):
        run, view = self.start(*JOINT, "--spec-first")
        need, question = self.review_need(view)
        run.answer(question, "Also greet in French when --fr is given", resolver_token=need["resolver_token"])
        view = run.advance_until_input()
        again, second = self.review_need(view)
        self.assertNotEqual(question, second)
        stages = self.stages(run)
        self.assertEqual(2, stages.count("requirements_gather"), stages)
        self.assertNotIn("astra_discovery", stages)

    def test_adaptive_planning_does_not_skip_a_spec_first_runs_requirements(self):
        run, view = self.start("--joint-planning", "--spec-first")
        self.review_need(view)
        self.assertIn("requirements_gather", self.stages(run))


if __name__ == "__main__":
    unittest.main()
