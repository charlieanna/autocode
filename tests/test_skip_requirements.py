"""An unattended worker's run: --skip-requirements answers by default, --skip-to-build also approves (#1731)."""

import json
import unittest
from types import SimpleNamespace

import autocode_skip_requirements as skip

from tests import test_planning


def events(state, kind):
    return [event for event in state.get("user_events") or [] if event.get("kind") == kind]


class SettingsTests(unittest.TestCase):
    def test_skip_to_build_implies_skip_requirements_and_needs_the_plan_reviewer(self):
        args = SimpleNamespace(skip_requirements=False, skip_to_build=True)
        self.assertEqual({"skip_requirements": True, "skip_to_build": True}, skip.new_run_settings(args, True))
        with self.assertRaisesRegex(ValueError, "Plan Reviewer"):
            skip.new_run_settings(args, False)
        only = SimpleNamespace(skip_requirements=True, skip_to_build=False)
        self.assertEqual({"skip_requirements": True}, skip.new_run_settings(only, False))
        self.assertEqual({}, skip.new_run_settings(SimpleNamespace(), True))

    def test_a_saved_run_keeps_the_choice_it_started_with(self):
        started = {"skip_requirements": True}
        self.assertFalse(skip.resume_refused(started, SimpleNamespace(skip_requirements=True)))
        self.assertFalse(skip.resume_refused(started, SimpleNamespace()))
        self.assertTrue(skip.resume_refused(started, SimpleNamespace(skip_to_build=True)))
        self.assertTrue(skip.resume_refused({}, SimpleNamespace(skip_requirements=True)))


class WorkerFlowTests(unittest.TestCase):
    setUp = test_planning.JointFlow.setUp
    launch = test_planning.JointFlow.launch
    saved = test_planning.JointFlow.saved
    prepare = test_planning.JointFlow.prepare
    new_run_engine_args = ()

    def test_skip_requirements_answers_by_default_and_still_waits_for_approval(self):
        self.prepare()
        result = self.launch(["Build a greeting tool", "--no-chat", "--skip-requirements"], 2)
        _, state = self.saved()
        self.assertEqual("AWAITING_GOAL_APPROVAL", state["status"], state.get("stop_reason"))
        delegated = events(state, "delegated")
        self.assertEqual(["Q1"], [event["question_id"] for event in delegated])
        self.assertEqual({skip.DELEGATED}, {event["source"] for event in delegated})
        self.assertIn("--skip-requirements: answered by default: Q1", result.stdout)
        self.assertEqual([], events(state, "goal_approval"), "never approves its own plan")
        self.assertIn("astra_challenge", [row["stage"] for row in state["stages"]], "the Plan Reviewer still runs")
        self.assertFalse((self.project / "greet.py").exists())

    def test_skip_to_build_approves_the_reviewed_plan_and_builds(self):
        self.prepare()
        result = self.launch(["Build a greeting tool", "--no-chat", "--skip-to-build"], 0)
        run, state = self.saved()
        self.assertEqual("COMPLETE", state["phase"])
        approvals = events(state, "goal_approval")
        self.assertEqual([skip.APPROVED], [event["source"] for event in approvals])
        self.assertEqual("user_cli", approvals[0]["actor"])
        self.assertIn("--skip-to-build", approvals[0]["provenance"]["command"])
        self.assertIn("--skip-to-build: plan", result.stdout)
        stages = [row["stage"] for row in state["stages"]]
        self.assertLess(stages.index("astra_challenge"), stages.index("terra"))
        view = json.loads(self.launch(["--run-dir", str(run), "--status", "--json"], 0).stdout)["view"]
        self.assertEqual(skip.APPROVED, view["approvals"][0]["source"])
        self.assertEqual(skip.APPROVED, view["approved_contract"]["approval_source"])
        self.assertIn("automatically, as --skip-to-build asked", self.launch(["--run-dir", str(run)], 0).stdout)

    def test_a_question_without_a_safe_default_still_stops_for_a_person(self):
        self.prepare("planning-blocked")
        self.launch(["Build a greeting tool", "--no-chat", "--skip-to-build"], 2)
        _, state = self.saved()
        self.assertEqual("WAITING_FOR_USER", state["status"])
        undelegable = [q["id"] for q in state["pending_questions"] if not q.get("delegable")]
        self.assertTrue(undelegable)
        self.assertFalse({event["question_id"] for event in events(state, "delegated")} & set(undelegable))
        self.assertEqual([], events(state, "goal_approval"))

    def test_the_flag_is_a_new_run_choice(self):
        self.prepare()
        self.launch(["Build a greeting tool", "--no-chat"], 2)
        run, state = self.saved()
        result = self.launch(["--run-dir", str(run), "--no-chat", "--skip-to-build"], 2)
        self.assertIn("chosen when a run starts", result.stdout + result.stderr)
        self.assertEqual("WAITING_FOR_USER", self.saved()[1]["status"])


if __name__ == "__main__":
    unittest.main()
