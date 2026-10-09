"""Upgrade approved, idle three-role OpenCode runs without repeating planning."""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import autocode as runner
import autocode_goals as goals
import autocode_opencode as oc
import test_planning
from goal_fixtures import approve_fixture


class JointUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.state = {"version": 2, "workspace": "/fixture", "task": "Keep approved work",
            "iteration": 22, "acceptance_criteria": [], "stages": [], "history": [],
            "sessions": {"astra": "ses_astra", "terra": "ses_terra", "sol": "ses_sol"},
            "settings": {"engine": "opencode", "transport_identity": {"engine": "opencode"},
                "limits": {"iteration_ceiling": None, "idle_timeout_seconds": 0, "tool_timeout_seconds": 0},
                "report_repair": {"max_attempts": 2},
                "roles": {"astra": {"model": "openai/gpt-6-astra", "reasoning_effort": "high"},
                          "terra": {"model": "zai-coding-plan/glm-5.3", "reasoning_effort": "max"},
                          "sol": {"model": "cursor-acp/grok-4.7-xhigh", "reasoning_effort": "high"}}}}
        approve_fixture(self.state, goals)
        self.state.update(status="PAUSED_TRANSPORT_CHANGED", next_stage="sol")
        self.args = test_planning.PlanningTests.configure_args(self, joint_planning=True)
        for name in ("check_models", "check_subscription_routes"):
            mock = patch.object(oc, name)
            mock.start()
            self.addCleanup(mock.stop)

    def test_explicit_upgrade_keeps_roles_sessions_and_approved_work(self):
        before = copy.deepcopy(self.state)
        settings = runner.configure(self.args, self.state)
        self.assertEqual(before, self.state)
        self.assertTrue(settings["joint_planning"])
        self.assertEqual("zai-coding-plan/glm-5.3", settings["roles"]["glm"]["model"])
        self.assertEqual("opencode", settings["roles"]["glm"]["engine"])
        for role, cfg in before["settings"]["roles"].items():
            self.assertEqual(cfg, settings["roles"][role])
        self.assertEqual(before["settings"]["limits"], settings["limits"])
        self.assertEqual(settings["transport_identity"], settings["transport_identities"]["opencode"])
        self.assertTrue(goals.approved({**self.state, "settings": settings}))

    def test_resume_without_explicit_upgrade_keeps_existing_workflow(self):
        settings = runner.configure(test_planning.PlanningTests.configure_args(self), self.state)
        self.assertNotIn("glm", settings["roles"])
        self.assertFalse(settings.get("joint_planning"))

    def test_unresolved_attempts_and_unapproved_plans_cannot_upgrade(self):
        for change in ({"active_stage": {"pid": 1}}, {"pending_report_repair": {"role": "terra"}},
                       {"uncertain_artifacts": "partial"}, {"next_stage": "astra_discovery"},
                       {"status": "TASK_COMPLETE"}, {"user_events": []}):
            state = {**copy.deepcopy(self.state), **change}
            before = copy.deepcopy(state)
            with self.subTest(change=change), self.assertRaises(ValueError):
                runner.configure(self.args, state)
            self.assertEqual(before, state)

    def test_codex_session_is_never_reassigned_to_glm_or_opencode(self):
        self.state["settings"]["roles"]["astra"]["engine"] = "codex"
        with self.assertRaisesRegex(ValueError, "different session engines"):
            runner.configure(self.args, self.state)

    def test_provider_preflight_failure_preserves_checkpoint(self):
        before = copy.deepcopy(self.state)
        with patch.object(oc, "check_models", side_effect=RuntimeError("model unavailable")), self.assertRaises(RuntimeError):
            runner.configure(self.args, self.state)
        self.assertEqual(before, self.state)


class JointUpgradeFlow(unittest.TestCase):
    setUp = test_planning.JointFlow.setUp
    launch = test_planning.JointFlow.launch
    saved = test_planning.JointFlow.saved
    prepare = test_planning.JointFlow.prepare
    draft = test_planning.JointFlow.draft
    new_run_engine_args = ()

    def test_resume_validates_existing_work_then_uses_glm_on_future_feedback(self):
        run, state = self.draft()
        args = ["--run-dir", str(run), "--no-chat"]
        self.launch([*args, "--approve-goal", state["displayed_goal"]], 0)
        self.launch([*args, "--pause-after-stage"], 2)
        state = self.saved()[1]
        state["settings"].pop("joint_planning")
        state["settings"]["roles"].pop("glm")
        state["settings"].pop("transport_identities")
        state.pop("planning")
        state["sessions"].pop("glm", None)
        (run / "state.json").write_text(json.dumps(state))
        self.launch([*args, "--joint-planning", "--resume-paused", "--pause-after-stage"], 2)
        upgraded = self.saved()[1]
        self.assertEqual(state["goal_contract"], upgraded["goal_contract"])
        self.assertEqual(state["current_task"], upgraded["current_task"])
        self.assertEqual(state["stages"], upgraded["stages"][:-1])
        self.assertEqual("sol", upgraded["stages"][-1]["stage"])
        self.assertEqual("opencode", upgraded["stages"][-1]["engine"])
        self.assertNotIn("planning", upgraded)
        self.assertEqual({"astra", "terra", "sol", "completion", "glm"}, set(upgraded["settings"]["roles"]))
        self.assertEqual(state, json.loads(Path(upgraded["planning_migrations"][-1]["backup"]).read_text()))
        runner.interventions.submit(self.project, run, request_id="revise-with-glm", kind="feedback",
                                    text="Keep the same deliverable and document invocation.")
        self.launch(args, 2)
        self.launch([*args, "--resume-paused", "--pause-after-stage"], 2)
        revised = self.saved()[1]
        self.assertEqual("glm", revised["stages"][-1]["role"])
        self.assertEqual("astra_challenge", revised["next_stage"])
        self.assertFalse(goals.approved(revised))


if __name__ == "__main__":
    unittest.main()
