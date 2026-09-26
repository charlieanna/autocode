import copy
import copy
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import autocode as runner
import autocode_goals as goals
import autocode_planning as planning
import autocode_support as support
from goal_fixtures import body


def requirements():
    value = body()
    for field in goals.BRIEF_FIELDS:
        value.pop(field)
    return value


class V2FlowTests(unittest.TestCase):
    def state(self):
        return {"task_id": "task", "task": "Task", "settings": {"joint_planning": True, "planning_flow": "v2"},
                "answers": {}, "user_events": [], "acceptance_criteria": []}

    def test_requirements_are_contract_free_and_strict(self):
        state = self.state()
        planning.apply(state, "requirements", {"requirements": requirements(), "summary": "clear"},
                       {"output": "requirements.json", "sha256": "abc"})
        self.assertNotIn("goal_contract", state)
        identity = state["planning_artifacts"]["requirements"]["artifact"]
        self.assertEqual("requirements:" + identity["sha256"], state["requirements_artifact_token"])
        self.assertEqual("plan", state["next_stage"])
        invalid = requirements(); invalid["milestones"] = []
        with self.assertRaises(ValueError):
            goals.validate_requirements_body(state, invalid)
        invalid = requirements()
        invalid["acceptance_criteria"][0].update(criterion="", verification_method="")
        with self.assertRaisesRegex(ValueError, "behavior and verification method"):
            goals.validate_requirements_body(state, invalid)

    def test_requirements_need_nonempty_acceptance_criteria_before_plan(self):
        state = self.state()
        invalid = requirements()
        invalid["acceptance_criteria"] = []
        with self.assertRaisesRegex(ValueError, "stable ID, behavior and verification method"):
            goals.apply_requirements(state, invalid, artifact_sha256="empty")
        self.assertNotIn("requirements_artifact_token", state)
        self.assertNotIn("next_stage", state)

        invalid_plan = body()
        invalid_plan["acceptance_criteria"] = []
        with self.assertRaisesRegex(ValueError, "stable ID, behavior and verification method"):
            goals.validate_body(self.state(), invalid_plan)

        valid_state = self.state()
        goals.apply_requirements(valid_state, requirements(), artifact_sha256="valid")
        self.assertEqual("plan", valid_state["next_stage"])
        self.assertNotIn("goal_contract", valid_state)
        pending = requirements()
        pending["open_blocking_questions"] = [{"id": "Q1", "question": "Which?", "why": "Scope",
                                                 "options": [], "proposed_default": ""}]
        pending_state = self.state()
        goals.apply_requirements(pending_state, pending, artifact_sha256="pending")
        self.assertEqual("WAITING_FOR_USER", pending_state["status"])

    def test_no_contract_answer_feedback_and_render_use_requirements_token(self):
        state = self.state(); value = requirements()
        value["open_blocking_questions"] = [{"id": "Q1", "question": "Which?", "why": "Scope", "options": [], "proposed_default": ""}]
        goals.apply_requirements(state, value, artifact_sha256="abc")
        self.assertIn("Q1", goals.render(state))
        goals.answer(state, "Q1", "CLI")
        self.assertEqual("requirements:abc", state["answers"]["Q1"]["contract_token"])
        goals.feedback(state, "Clarify output")
        self.assertEqual("requirements:abc", state["brief_feedback"][-1]["contract_token"])
        goals.apply_intervention_feedback(state, {"id": "feedback", "text": "Clarify again",
                                                  "observed_goal_token": "stale-token"}, {"applied_at": "now"})
        self.assertEqual("requirements:abc", state["brief_feedback"][-1]["contract_token"])
        self.assertNotIn("goal_contract", state)

    def test_non_v2_no_contract_render_remains_byte_identical(self):
        self.assertEqual("No contract yet; resume to interview with Astra.",
                         goals.render({"settings": {}, "status": "RUNNING"}))

    def test_v2_resolver_and_review_coverage(self):
        state = self.state()
        self.assertEqual("requirements", planning.entry_stage(state))
        self.assertEqual("plan_revise", planning.next_after(state, "plan_review"))
        state["planning"] = {"astra_calls": 0, "reports": {"plan_review": {"report": {"concerns": [
            {"id": "C1", "concern": "issue", "evidence_refs": ["x"], "requested_change": "fix", "acceptance_test": "test", "blocking": True}]}}}}
        revised = body(); revised["initial_task"] = {"objective": "x", "affected_paths": ["x"], "kind": "implement", "milestone_id": "M1", "requirements": ["x"], "acceptance_criteria": ["C1"], "validation_plan": ["x"]}
        revised["milestones"][0]["boundaries"] = ["x.py"]
        planning.apply(state, "plan_revise", {"contract": revised, "summary": "fixed", "responses": [
            {"concern_id": "C1", "response": "fixed", "evidence_refs": ["x"], "change": "x", "acceptance_test": "x"}]}, {"output": "revision"})
        self.assertEqual("plan_finalize", state["next_stage"])

    def test_v2_review_budget_stays_at_two_calls(self):
        state = self.state()
        state["planning"] = {"astra_calls": 0, "reports": {}, "final_token": None}
        planning.charge(state, "plan_review")
        planning.charge(state, "plan_finalize")
        with self.assertRaisesRegex(planning.s.Paused, "Two plan-review calls used"):
            planning.charge(state, "plan_finalize")

    def test_v2_roles_routes_and_context_engine_are_consistent(self):
        args = SimpleNamespace(astra_model=None, terra_model=None, sol_model=None,
                               glm_model="zai-coding-plan/glm-5.3-flash")
        settings = {"engine": "opencode", "roles": {role: {} for role in ("astra", "terra", "sol")},
                    "transport_identity": {"engine": "opencode"}}
        runner.configure_joint(settings, args, fresh=True)
        self.assertEqual("v2", settings["planning_flow"])
        for role in ("requirements_planner", "technical_planner"):
            self.assertEqual("zai-coding-plan/glm-5.3-flash", settings["roles"][role]["model"])
            self.assertEqual("opencode", settings["roles"][role]["engine"])
        self.assertEqual(planning.PINNED_REVIEWER_MODEL, settings["roles"]["plan_reviewer"]["model"])
        self.assertTrue(settings["roles"]["plan_reviewer"]["model_pinned"])
        state = {"task": "Task", "workspace": str(Path.cwd()), "settings": settings}
        expected = {"requirements": "requirements_planner", "plan": "technical_planner",
                    "plan_revise": "technical_planner", "plan_review": "plan_reviewer",
                    "plan_finalize": "plan_reviewer"}
        for stage, role in expected.items():
            with self.subTest(stage=stage):
                self.assertTrue(planning.is_planning(state, stage))
                self.assertEqual(role, planning.role_for(state, stage))
                self.assertEqual(role, planning.route_for(state, stage))
                with patch.object(support, "snapshot", return_value={"revision": "fixture", "head": "fixture"}):
                    packet = json.loads(support.context_packet(state, stage, Path("state.json"))[0].split("CURRENT HANDOFF DATA\n", 1)[1])
                self.assertEqual(planning.engine_for(settings, planning.route_for(state, stage)),
                                 packet["execution_engine"])

    def test_v2_resume_updates_both_planners_but_not_the_pinned_reviewer(self):
        args = SimpleNamespace(astra_model=None, terra_model=None, sol_model=None, glm_model=None)
        settings = {"engine": "opencode", "roles": {role: {} for role in ("astra", "terra", "sol")},
                    "transport_identity": {"engine": "opencode"}}
        runner.configure_joint(settings, args, fresh=True)
        reviewer = copy.deepcopy(settings["roles"]["plan_reviewer"])
        args.glm_model = "zai-coding-plan/glm-5.3-flash"
        runner.configure_joint(settings, args, fresh=False)
        self.assertEqual("zai-coding-plan/glm-5.3-flash", settings["roles"]["requirements_planner"]["model"])
        self.assertEqual("zai-coding-plan/glm-5.3-flash", settings["roles"]["technical_planner"]["model"])
        self.assertEqual(reviewer, settings["roles"]["plan_reviewer"])

    def test_v2_stages_use_fresh_read_only_routes_and_planning_metadata(self):
        evidence = Path.cwd() / ".autocode" / "evidence" / "planning-flow-v2-workspaces"
        evidence.mkdir(parents=True, exist_ok=True)
        root = Path(tempfile.mkdtemp(prefix="m3-", dir=evidence))
        self.addCleanup(shutil.rmtree, root, True)
        run = root / "run"; run.mkdir()
        schema = run / "schema.json"; schema.write_text(json.dumps({"type": "object", "properties": {}}))
        args = SimpleNamespace(astra_model=None, terra_model=None, sol_model=None, glm_model=None)
        settings = {"engine": "opencode", "roles": {role: {} for role in ("astra", "terra", "sol")},
                    "transport_identity": {"engine": "opencode"}}
        runner.configure_joint(settings, args, fresh=True)
        state = {"settings": settings, "iteration": 1, "stages": [],
                 "sessions": {role: "saved-" + role for role in settings["roles"]}, "planning_artifacts": {}}
        for previous in ("requirements", "plan", "plan_review", "plan_revise"):
            artifact_path = "planning/" + previous + ".json"
            delta_path = "planning/" + previous + ".delta.json"
            for relative in (artifact_path, delta_path):
                path = run / relative; path.parent.mkdir(parents=True, exist_ok=True); path.write_text("{}\n")
            state["planning_artifacts"][previous] = {
                "artifact": {"path": artifact_path, "sha256": hashlib.sha256((run / artifact_path).read_bytes()).hexdigest()},
                "delta": {"path": delta_path, "sha256": hashlib.sha256((run / delta_path).read_bytes()).hexdigest()},
            }
        for stage, role in planning.V2_STAGE_ROLES.items():
            with self.subTest(stage=stage):
                state["next_stage"] = stage
                value, record = runner.run_role(role=role, prompt="fixture", sandbox="read-only", workspace=root,
                    run_dir=run, state=state, schema=schema, model=settings["roles"][role]["model"],
                    allow_write=False, dry_run=True)
                self.assertEqual({"status": "DRY_RUN"}, value)
                self.assertTrue(record["planning"])
                self.assertIsNone(record["expected_session"])
                self.assertEqual(settings["roles"][role]["model"], record["command"][record["command"].index("--model") + 1])

    def test_v2_predecessor_failures_stop_before_the_provider_launch(self):
        args = SimpleNamespace(astra_model=None, terra_model=None, sol_model=None, glm_model=None)
        settings = {"engine": "opencode", "roles": {role: {} for role in ("astra", "terra", "sol")},
                    "transport_identity": {"engine": "opencode"}}
        runner.configure_joint(settings, args, fresh=True)
        state = {"settings": settings, "next_stage": "plan", "iteration": 1, "sessions": {}, "stages": []}
        with patch.object(runner.opencode, "launch", side_effect=AssertionError("provider launch")):
            with self.assertRaises(support.Paused) as error:
                runner.run_role(role="technical_planner", prompt="fixture", sandbox="read-only", workspace=Path.cwd(),
                    run_dir=Path.cwd() / ".autocode" / "evidence", state=state, schema=Path("unused.json"),
                    model=settings["roles"]["technical_planner"]["model"], allow_write=False, dry_run=True)
        self.assertEqual("PAUSED_INVALID_PREDECESSOR", error.exception.status)

    def test_v2_reviewer_route_fails_closed_without_the_pinned_cursor_role(self):
        args = SimpleNamespace(astra_model=None, terra_model=None, sol_model=None, glm_model=None)
        settings = {"engine": "opencode", "roles": {role: {} for role in ("astra", "terra", "sol")},
                    "transport_identity": {"engine": "opencode"}}
        runner.configure_joint(settings, args, fresh=True)
        for update in ({"remove": True}, {"model": "openai/gpt-5.6-sol"}, {"model_pinned": False}):
            with self.subTest(update=update):
                state = {"settings": copy.deepcopy(settings)}
                if update.get("remove"):
                    state["settings"]["roles"].pop("plan_reviewer")
                else:
                    state["settings"]["roles"]["plan_reviewer"].update(update)
                for stage in ("plan_review", "plan_finalize"):
                    with self.assertRaises(support.Paused) as error:
                        planning.route_for(state, stage)
                    self.assertEqual("PAUSED_REVIEWER_ROUTE", error.exception.status)

    def test_v2_predecessor_gate_prevents_a_provider_attempt(self):
        args = SimpleNamespace(astra_model=None, terra_model=None, sol_model=None, glm_model=None)
        settings = {"engine": "opencode", "roles": {role: {} for role in ("astra", "terra", "sol")},
                    "transport_identity": {"engine": "opencode"}}
        runner.configure_joint(settings, args, fresh=True)
        state = {"settings": settings, "next_stage": "plan", "iteration": 1, "sessions": {}, "stages": []}
        with self.assertRaises(support.Paused) as error:
            runner.run_role(role="technical_planner", prompt="fixture", sandbox="read-only", workspace=Path.cwd(),
                            run_dir=Path.cwd() / ".autocode" / "evidence", state=state, schema=Path("unused.json"),
                            model=settings["roles"]["technical_planner"]["model"], allow_write=False, dry_run=True)
        self.assertEqual("PAUSED_INVALID_PREDECESSOR", error.exception.status)
        self.assertEqual([], state["stages"])

    def test_v2_stage_names_do_not_overlap_figma_or_legacy_runner_names(self):
        state = self.state()
        figma_stages = {"requirements_planner", "plan_reviewer", "requirements_revision", "plan_finalizer", "builder", "validator"}
        runner_stages = set(planning.STAGES) | {"astra_plan", "astra_review", "astra_checkpoint", "terra", "sol", "completion"}
        self.assertFalse(set(planning.V2_STAGES) & figma_stages)
        self.assertFalse(set(planning.V2_STAGES) & runner_stages)
        for stage in figma_stages:
            with self.subTest(stage=stage):
                self.assertFalse(planning.is_planning(state, stage))
        self.assertEqual("astra", planning.role_for(state, "astra_plan"))
