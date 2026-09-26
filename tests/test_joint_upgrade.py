"""Upgrade approved, idle three-role OpenCode runs without repeating planning."""
import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import autocode as runner
import autocode_goals as goals
import autocode_opencode as oc
import autocode_planning as planning
import autocode_planning_graph as planning_graph
import autocode_support as support
import test_planning
from goal_fixtures import approve_fixture, body


class JointUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.transport_patch = patch.object(runner, "opencode", oc)
        self.transport_patch.start()
        self.addCleanup(self.transport_patch.stop)
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
        self.assertEqual("v2", settings["planning_flow"])
        self.assertEqual("zai-coding-plan/glm-5.3", settings["roles"]["glm"]["model"])
        self.assertEqual("opencode", settings["roles"]["glm"]["engine"])
        for role in ("requirements_planner", "technical_planner"):
            self.assertEqual("zai-coding-plan/glm-5.3", settings["roles"][role]["model"])
            self.assertEqual("opencode", settings["roles"][role]["engine"])
        self.assertEqual("cursor-acp/claude-opus-5-5-high", settings["roles"]["plan_reviewer"]["model"])
        self.assertTrue(settings["roles"]["plan_reviewer"]["model_pinned"])
        for role, cfg in before["settings"]["roles"].items():
            self.assertEqual(cfg, settings["roles"][role])
        expected_limits = {**before["settings"]["limits"], "max_seconds": None,
                           "max_reported_tokens": None, "no_progress_batches": 0}
        self.assertEqual(expected_limits, settings["limits"])
        self.assertEqual(settings["transport_identity"], settings["transport_identities"]["opencode"])
        self.assertTrue(goals.approved({**self.state, "settings": settings}))

    def test_enable_saved_joint_installs_v2_routes_before_provider_preflight(self):
        with patch.object(oc, "check_models") as check_models, \
             patch.object(oc, "check_subscription_routes") as check_subscription:
            runner.configure(self.args, self.state)
        for check in (check_models, check_subscription):
            self.assertEqual({"requirements_planner", "technical_planner", "plan_reviewer"},
                             {"requirements_planner", "technical_planner", "plan_reviewer"} & set(check.call_args.args[0]))

    def test_resume_without_explicit_upgrade_keeps_existing_workflow(self):
        settings = runner.configure(test_planning.PlanningTests.configure_args(self), self.state)
        self.assertNotIn("glm", settings["roles"])
        self.assertFalse(settings.get("joint_planning"))

    def test_unresolved_attempts_and_nonplanning_unapproved_runs_cannot_upgrade(self):
        for change in ({"active_stage": {"pid": 1}}, {"pending_report_repair": {"role": "terra"}},
                       {"uncertain_artifacts": "partial"},
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


class LegacyPlanningMigrationTests(unittest.TestCase):
    def setUp(self):
        evidence = Path.cwd() / ".autocode" / "evidence" / "joint-upgrade-test-workspaces"
        evidence.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix="m4-", dir=evidence)).resolve()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.run = self.root / "run"
        self.run.mkdir()
        self.args = test_planning.PlanningTests.configure_args(self, joint_planning=True)
        self.transport_identity = {"engine": "opencode", "identity_version": 2,
                                   "executable": "/fixture/opencode", "version": "1.18.31",
                                   "config_hashes": {}, "environment_config_hashes": {}}
        for name in ("check_models", "check_subscription_routes", "local_settings"):
            mock = patch.object(oc, name)
            if name == "local_settings":
                mock = patch.object(oc, name, return_value=copy.deepcopy(self.transport_identity))
            mock.start()
            self.addCleanup(mock.stop)

    def legacy_state(self, next_stage, *, status="RUNNING"):
        transport_identity = copy.deepcopy(getattr(self, "transport_identity", {
            "engine": "opencode", "identity_version": 2, "executable": "/fixture/opencode",
            "version": "1.18.31", "config_hashes": {}, "environment_config_hashes": {}}))
        discovery = {"contract": body(), "summary": "Legacy draft", "code_refs": [],
                     "alternatives": [], "uncertainties": []}
        challenge = {"summary": "Legacy review", "concerns": [{
            "id": "C1", "concern": "Need a regression", "evidence_refs": ["tools/test_planning.py"],
            "requested_change": "Add coverage", "acceptance_test": "Focused test passes", "blocking": True}]}
        revise = {"contract": body(), "summary": "Legacy revision", "code_refs": [], "responses": [{
            "concern_id": "C1", "response": "Coverage added", "evidence_refs": ["tools/test_joint_upgrade.py"],
            "change": "Added migration test", "acceptance_test": "Focused test passes"}]}
        state = {
            "version": 3, "workspace": str(self.root), "task": "Preserve legacy planning", "task_id": "legacy-task",
            "iteration": 4, "status": status, "phase": "PLANNING", "next_stage": next_stage,
            "sessions": {"astra": "legacy-astra", "terra": "terra-session", "sol": "sol-session"},
            "history": [{"stage": "terra", "output": "old-terra.json"}], "stages": [],
            "user_events": [{"kind": "answer", "id": "old-answer"}],
            "planning_history": [{"astra_calls": 2, "reports": {"old": {"output": "old.json"}}}],
            "settings": {"engine": "opencode", "transport_identity": transport_identity,
                         "limits": {"iteration_ceiling": 19, "max_seconds": None, "stage_timeout_seconds": 0,
                                    "idle_timeout_seconds": 0, "tool_timeout_seconds": 0,
                                    "max_reported_tokens": None, "no_progress_batches": 3,
                                    "automatic_retries": 0},
                         "report_repair": {"max_attempts": 2}, "roles": {
                             "astra": {"model": "openai/gpt-5.6-sol", "reasoning_effort": "high"},
                             "terra": {"model": "zai-coding-plan/glm-5.3", "reasoning_effort": "max"},
                             "sol": {"model": "cursor-acp/grok-4.7-xhigh", "reasoning_effort": "high"}}},
            "planning": {"astra_calls": 2 if status == "PAUSED_PLANNING_BUDGET" else
                         1 if next_stage in ("glm_revise", "astra_finalize") else 0,
                         "reports": {
                             "astra_discovery": {"report": discovery, "output": "legacy-discovery.json"},
                             "astra_challenge": {"report": challenge, "output": "legacy-challenge.json"},
                             "glm_revise": {"report": revise, "output": "legacy-revise.json"}},
                         "final_token": None},
        }
        if status == "WAITING_FOR_USER":
            state["pending_questions"] = [{"id": "Q1", "question": "Which output?", "why": "Scope",
                                           "options": [], "proposed_default": ""}]
        return state

    def migrate(self, state):
        before = copy.deepcopy(state)
        (self.run / "state.json").write_text(json.dumps(state))
        settings = runner.configure(self.args, state)
        self.assertEqual(before, state)
        self.assertTrue(runner.migrate_saved_legacy_planning(state, settings, self.run))
        return before

    def test_every_legacy_boundary_maps_only_completed_reports_to_v2_artifacts(self):
        cases = (
            ("astra_discovery", "RUNNING", "requirements", []),
            ("astra_challenge", "RUNNING", "plan_review", ["requirements", "plan"]),
            ("glm_revise", "RUNNING", "plan_revise", ["requirements", "plan", "plan_review"]),
            ("astra_finalize", "RUNNING", "plan_finalize", ["requirements", "plan", "plan_review", "plan_revise"]),
            ("astra_discovery", "WAITING_FOR_USER", "requirements", []),
            ("astra_finalize", "PAUSED_PLANNING_BUDGET", "plan_finalize",
             ["requirements", "plan", "plan_review", "plan_revise"]),
        )
        for next_stage, status, expected_stage, expected_artifacts in cases:
            with self.subTest(next_stage=next_stage, status=status):
                run = self.root / f"{next_stage}-{status}"
                run.mkdir()
                previous_run, self.run = self.run, run
                try:
                    state = self.legacy_state(next_stage, status=status)
                    before = self.migrate(state)
                finally:
                    self.run = previous_run
                self.assertEqual(expected_stage, state["next_stage"])
                self.assertEqual(status, state["status"])
                self.assertEqual(before["sessions"], state["sessions"])
                self.assertEqual(before["settings"]["limits"], state["settings"]["limits"])
                self.assertEqual(before["history"], state["history"])
                self.assertEqual(before["planning_history"], state["planning_history"])
                self.assertEqual(before["user_events"], state["user_events"])
                self.assertEqual("v2", state["settings"]["planning_flow"])
                self.assertTrue(state["settings"]["joint_planning"])
                for role in ("requirements_planner", "technical_planner", "plan_reviewer"):
                    self.assertIn(role, state["settings"]["roles"])
                reviewer = state["settings"]["roles"]["plan_reviewer"]
                self.assertEqual(planning.PINNED_REVIEWER_MODEL, reviewer["model"])
                self.assertTrue(reviewer["model_pinned"])
                migration = state["planning_migrations"][-1]
                self.assertEqual(expected_artifacts, migration["mapped_artifact_stages"])
                self.assertEqual(before, json.loads(Path(migration["backup"]).read_text()))
                self.assertEqual(expected_artifacts, list(state.get("planning_artifacts", {})))
                for stage in expected_artifacts:
                    for kind in ("artifact", "delta"):
                        identity = state["planning_artifacts"][stage][kind]
                        path = run / identity["path"]
                        self.assertTrue(path.is_file())
                        self.assertEqual(identity["sha256"], support.file_hash(path))
                self.assertFalse((run / "planning" / "final-plan.json").exists())
                self.assertFalse((run / "planning" / "graph.json").exists())
                self.assertIsNone(state["planning"]["final_token"])

    def test_active_and_report_repair_migrations_refuse_without_changing_state(self):
        for blocked in ({"active_stage": {"pid": 123}}, {"pending_report_repair": {"role": "astra"}}):
            with self.subTest(blocked=blocked):
                state = self.legacy_state("astra_challenge")
                state.update(blocked)
                settings = copy.deepcopy(state["settings"])
                runner.install_v2_planning_roles(settings, self.args)
                state_path = self.run / "state.json"
                before = json.dumps(state, sort_keys=True, separators=(",", ":"))
                state_path.write_text(before)
                with self.assertRaises(support.Paused) as error:
                    runner.migrate_saved_legacy_planning(state, settings, self.run)
                self.assertEqual("PAUSED_UNCERTAIN_STAGE", error.exception.status)
                self.assertEqual(before, json.dumps(state, sort_keys=True, separators=(",", ":")))
                self.assertEqual(before, state_path.read_text())

    def test_configure_refuses_an_active_explicit_legacy_migration_without_mutating_state(self):
        state = self.legacy_state("astra_challenge")
        state["active_stage"] = {"pid": 123}
        before = copy.deepcopy(state)
        with self.assertRaises(support.Paused) as error:
            runner.configure(self.args, state)
        self.assertEqual("PAUSED_UNCERTAIN_STAGE", error.exception.status)
        self.assertEqual(before, state)

    def test_changed_transport_pauses_before_migration_without_replacing_identity(self):
        state = self.legacy_state("astra_challenge")
        settings = runner.configure(self.args, state)
        before = copy.deepcopy(state)
        saved = json.dumps(state, sort_keys=True, separators=(",", ":"))
        (self.run / "state.json").write_text(saved)
        changed = {**self.transport_identity, "version": "1.18.32"}
        with patch.object(oc, "local_settings", return_value=changed), \
             patch.object(oc, "launch") as launch, \
             self.assertRaises(support.Paused) as error:
            runner.migrate_saved_legacy_planning(state, settings, self.run)
        self.assertEqual("PAUSED_TRANSPORT_CHANGED", error.exception.status)
        launch.assert_not_called()
        self.assertEqual(before, state)
        self.assertEqual(saved, (self.run / "state.json").read_text())

    def test_ineligible_legacy_report_restarts_at_requirements_without_partial_handoffs(self):
        state = self.legacy_state("astra_challenge")
        state["planning"]["reports"]["astra_discovery"]["report"]["contract"]["milestones"][0].pop("depends_on")
        self.migrate(state)
        migration = state["planning_migrations"][-1]
        self.assertEqual("requirements", state["next_stage"])
        self.assertEqual([], migration["mapped_artifact_stages"])
        self.assertEqual(["astra_discovery"], migration["skipped_reports"])
        self.assertNotIn("planning_artifacts", state)
        self.assertFalse((self.run / "planning").exists())

    def test_legacy_stages_remain_dispatchable_without_an_explicit_migration(self):
        state = self.legacy_state("glm_revise")
        state["settings"]["joint_planning"] = True
        self.assertTrue(planning.is_planning(state, "astra_discovery"))
        self.assertTrue(planning.is_planning(state, "astra_challenge"))
        self.assertTrue(planning.is_planning(state, "glm_revise"))
        self.assertTrue(planning.is_planning(state, "astra_finalize"))
        self.assertEqual("glm", planning.role_for(state, "glm_revise"))


class JointUpgradeFlow(unittest.TestCase):
    setUp = test_planning.JointFlow.setUp
    launch = test_planning.JointFlow.launch
    saved = test_planning.JointFlow.saved
    prepare = test_planning.JointFlow.prepare
    new_run_engine_args = ()

    def fixture_transport_identity(self, workspace):
        source = str(Path(__file__).resolve().parent)
        code = ("import json,sys;sys.path.insert(0," + repr(source) + ");"
                "import autocode_opencode as adapter;"
                "print(json.dumps(adapter.local_settings(sys.argv[1])))")
        probe = subprocess.run([sys.executable, "-c", code, str(workspace)], cwd=self.root,
                               env=self.env, capture_output=True, text=True, timeout=15)
        self.assertEqual(0, probe.returncode, probe.stderr)
        return json.loads(probe.stdout)

    def draft(self):
        self.prepare()
        self.launch(["Build a greeting tool", "--no-chat"], 2)
        run, state = self.saved()
        self.assertEqual("requirements_planner", state["stages"][0]["role"])
        self.assertEqual("WAITING_FOR_USER", state["status"])
        self.launch(["--run-dir", str(run), "--answer", "Q1=CLI"], 0)
        self.launch(["--run-dir", str(run), "--no-chat"], 2)
        return self.saved()

    def test_resume_validates_existing_work_then_uses_glm_on_future_feedback(self):
        run, state = self.draft()
        args = ["--run-dir", str(run), "--no-chat"]
        self.launch([*args, "--approve-goal", state["displayed_goal"]], 0)
        self.launch([*args, "--pause-after-stage"], 2)
        state = self.saved()[1]
        if state["next_stage"] == "terra":
            self.assertIsNone(state.get("implementation"))
            self.launch([*args, "--resume-paused", "--pause-after-stage"], 2)
            state = self.saved()[1]
        self.assertEqual("sol", state["next_stage"])
        self.assertTrue(state.get("implementation"))
        state["settings"].pop("joint_planning")
        state["settings"].pop("planning_flow")
        for role in ("glm", "requirements_planner", "technical_planner", "plan_reviewer"):
            state["settings"]["roles"].pop(role)
        state["settings"].pop("transport_identities")
        state.pop("planning")
        state["sessions"].pop("glm", None)
        (run / "state.json").write_text(json.dumps(state))
        self.launch([*args, "--joint-planning", "--resume-paused", "--pause-after-stage"], 2)
        upgraded = self.saved()[1]
        self.assertEqual(state["goal_contract"], upgraded["goal_contract"])
        self.assertEqual(state["current_task"], upgraded["current_task"])
        new_stages = upgraded["stages"][len(state["stages"]):]
        self.assertEqual("sol", new_stages[-1]["stage"])
        self.assertEqual("opencode", new_stages[-1]["engine"])
        self.assertEqual("astra_review", upgraded["next_stage"])
        self.assertNotIn("planning", upgraded)
<<<<<<< Updated upstream
        self.assertEqual({"astra", "terra", "sol", "completion", "requirements", "glm", "plan_reviewer"}, set(upgraded["settings"]["roles"]))
=======
        self.assertEqual({"astra", "terra", "sol", "completion", "glm", "requirements_planner",
                          "technical_planner", "plan_reviewer"}, set(upgraded["settings"]["roles"]))
        self.assertEqual("v2", upgraded["settings"]["planning_flow"])
>>>>>>> Stashed changes
        self.assertEqual(state, json.loads(Path(upgraded["planning_migrations"][-1]["backup"]).read_text()))
        runner.interventions.submit(self.project, run, request_id="revise-with-glm", kind="feedback",
                                    text="Keep the same deliverable and document invocation.")
        self.launch(args, 2)
        self.launch([*args, "--resume-paused", "--pause-after-stage"], 2)
        revised = self.saved()[1]
<<<<<<< Updated upstream
        self.assertEqual("requirements", revised["stages"][-1]["role"])
        self.assertEqual("astra_discovery", revised["next_stage"])
=======
        self.assertEqual("requirements_planner", revised["stages"][-1]["role"])
        self.assertEqual("plan", revised["next_stage"])
>>>>>>> Stashed changes
        self.assertFalse(goals.approved(revised))

    def test_migrated_legacy_fixture_finalizes_approves_and_consumes_graph(self):
        self.prepare()
        run = self.project / ".autocode" / "runs" / "migrated-legacy"
        run.mkdir(parents=True)
        legacy = LegacyPlanningMigrationTests.legacy_state(self, "astra_finalize")
        legacy["workspace"] = str(self.project)
        legacy["settings"]["transport_identity"] = self.fixture_transport_identity(self.project)
        goals.install_draft(legacy, body(), origin="astra_discovery")
        legacy.update(status="RUNNING", phase="PLANNING", next_stage="astra_finalize")
        before = copy.deepcopy(legacy)
        (run / "state.json").write_text(json.dumps(legacy))

        self.launch(["--run-dir", str(run), "--joint-planning", "--migrate-only", "--no-chat"], 0)
        legacy = self.saved()[1]
        migration = legacy["planning_migrations"][-1]
        self.assertEqual(before, json.loads(Path(migration["backup"]).read_text()))
        self.assertEqual(before["sessions"], legacy["sessions"])
        self.assertEqual(before["history"], legacy["history"])
        self.assertEqual(before["planning_history"], legacy["planning_history"])
        self.assertEqual(before["goal_contract"], legacy["goal_contract"])
        self.assertEqual(["requirements", "plan", "plan_review", "plan_revise"], migration["mapped_artifact_stages"])
        self.assertEqual("plan_finalize", legacy["next_stage"])
        self.assertFalse((run / "planning" / "final-plan.json").exists())
        self.assertFalse((run / "planning" / "graph.json").exists())

        self.launch(["--run-dir", str(run), "--no-chat"], 2)
        finalized = self.saved()[1]
        self.assertEqual("AWAITING_GOAL_APPROVAL", finalized["status"], finalized.get("stop_reason"))
        self.assertEqual("plan_finalize", finalized["stages"][-1]["stage"])
        self.assertEqual(2, finalized["planning"]["astra_calls"])
        self.assertTrue((run / "planning" / "final-plan.json").is_file())
        self.assertTrue((run / "planning" / "graph.json").is_file())

        self.launch(["--run-dir", str(run), "--approve-goal", finalized["displayed_goal"]], 0)
        approved = self.saved()[1]
        approval = approved["goal_contract"]["approval_event"]
        self.assertEqual(approved["displayed_goal"], approval["token"])
        self.assertIn(approval, approved["user_events"])
        stages_before_consume = list(approved["stages"])
        self.assertEqual("ready", planning_graph.consume(approved, run)["status"])
        self.assertEqual(stages_before_consume, approved["stages"])


if __name__ == "__main__":
    unittest.main()
