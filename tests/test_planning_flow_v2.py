import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import autocode as runner
import autocode_goal_lifecycle as lifecycle
import autocode_goals as goals
import autocode_planning as planning
import autocode_resolver_human as human
import autocode_stage_context as stage_context
import autocode_support as support
import autopilot
from goal_fixtures import body


def requirements():
    value = body()
    for field in goals.BRIEF_FIELDS:
        value.pop(field)
    return value


class V2FlowTests(unittest.TestCase):
    def state(self):
        return {
            "task_id": "task",
            "task": "Task",
            "settings": {"joint_planning": True, "planning_flow": "v2"},
            "answers": {},
            "user_events": [],
            "acceptance_criteria": [],
        }

    def test_requirements_are_contract_free_and_strict(self):
        state = self.state()
        autopilot.apply_planning(
            state,
            "requirements",
            {"requirements": requirements(), "summary": "clear"},
            {"output": "requirements.json", "sha256": "abc"},
        )
        self.assertNotIn("goal_contract", state)
        identity = state["planning_artifacts"]["requirements"]["artifact"]
        self.assertEqual("requirements:" + identity["sha256"], state["requirements_artifact_token"])
        self.assertEqual("plan", state["next_stage"])
        invalid = requirements()
        invalid["milestones"] = []
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
            lifecycle.apply_requirements(state, invalid, artifact_sha256="empty")
        self.assertNotIn("requirements_artifact_token", state)
        self.assertNotIn("next_stage", state)

        invalid_plan = body()
        invalid_plan["acceptance_criteria"] = []
        with self.assertRaisesRegex(ValueError, "stable ID, behavior and verification method"):
            lifecycle.validate_body(self.state(), invalid_plan)

        valid_state = self.state()
        lifecycle.apply_requirements(valid_state, requirements(), artifact_sha256="valid")
        self.assertEqual("plan", valid_state["next_stage"])
        self.assertNotIn("goal_contract", valid_state)
        pending = requirements()
        pending["open_blocking_questions"] = [
            {"id": "Q1", "question": "Which?", "why": "Scope", "options": [], "proposed_default": ""}
        ]
        pending_state = self.state()
        lifecycle.apply_requirements(pending_state, pending, artifact_sha256="pending")
        self.assertEqual("RESOLVER_PENDING", pending_state["status"])
        self.assertEqual([], pending_state["pending_questions"])
        self.assertEqual(pending["open_blocking_questions"], human.internal_questions(pending_state))
        self.assertIsNone(human.current(pending_state))

    def test_no_contract_answer_feedback_and_render_use_requirements_token(self):
        state = self.state()
        value = requirements()
        value["open_blocking_questions"] = [
            {"id": "Q1", "question": "Which?", "why": "Scope", "options": [], "proposed_default": ""}
        ]
        lifecycle.apply_requirements(state, value, artifact_sha256="abc")
        self.assertEqual("escalate", human.evaluate(state))
        self.assertIn("Q1", lifecycle.render(state))
        goals.answer(state, "Q1", "CLI")
        self.assertEqual("requirements:abc", state["answers"]["Q1"]["contract_token"])
        goals.feedback(state, "Clarify output")
        self.assertEqual("requirements:abc", state["brief_feedback"][-1]["contract_token"])
        goals.apply_intervention_feedback(
            state,
            {"id": "feedback", "text": "Clarify again", "observed_goal_token": "stale-token"},
            {"applied_at": "now"},
        )
        self.assertEqual("stale-token", state["brief_feedback"][-1]["contract_token"])
        self.assertNotIn("goal_contract", state)

    def test_non_v2_no_contract_render_remains_byte_identical(self):
        self.assertEqual(
            "No contract yet; resume to interview with the Requirements Gatherer.",
            lifecycle.render({"settings": {}, "status": "RUNNING"}),
        )

    def test_v2_is_explicit_opt_in_and_default_stage_role_contract_is_unchanged(self):
        default = {
            "settings": {
                "joint_planning": True,
                "roles": {
                    "requirements": {"model": "requirements-model"},
                    "glm": {"model": "technical-model"},
                    "plan_reviewer": {"model": "reviewer-model"},
                },
            }
        }
        self.assertEqual("requirements_gather", planning.entry_stage(default))
        self.assertTrue(planning.is_planning(default, "astra_discovery"))
        self.assertFalse(planning.is_planning(default, "requirements"))
        self.assertEqual("glm", planning.role_for(default, "astra_discovery"))
        self.assertEqual("plan_reviewer", planning.route_for(default, "astra_challenge"))
        self.assertEqual("requirements-model", default["settings"]["roles"]["requirements"]["model"])
        self.assertEqual("technical-model", default["settings"]["roles"]["glm"]["model"])
        self.assertEqual("reviewer-model", default["settings"]["roles"]["plan_reviewer"]["model"])

    def test_v2_resolver_and_review_coverage(self):
        state = self.state()
        self.assertEqual("requirements", planning.entry_stage(state))
        self.assertEqual("plan_revise", planning.next_after(state, "plan_review"))
        state["planning"] = {
            "astra_calls": 0,
            "reports": {
                "plan_review": {
                    "report": {
                        "concerns": [
                            {
                                "id": "C1",
                                "concern": "issue",
                                "evidence_refs": ["x"],
                                "requested_change": "fix",
                                "acceptance_test": "test",
                                "blocking": True,
                            }
                        ]
                    }
                }
            },
        }
        revised = body()
        revised["initial_task"] = {
            "objective": "x",
            "affected_paths": ["x"],
            "kind": "implement",
            "milestone_id": "M1",
            "requirements": ["x"],
            "acceptance_criteria": ["C1"],
            "validation_plan": ["x"],
        }
        revised["milestones"][0]["affected_paths"] = ["x.py"]
        autopilot.apply_planning(
            state,
            "plan_revise",
            {
                "contract": revised,
                "summary": "fixed",
                "responses": [
                    {
                        "concern_id": "C1",
                        "response": "fixed",
                        "evidence_refs": ["x"],
                        "change": "x",
                        "acceptance_test": "x",
                    }
                ],
            },
            {"output": "revision"},
        )
        self.assertEqual("plan_finalize", state["next_stage"])

    def test_v2_review_budget_stays_at_two_calls(self):
        state = self.state()
        state["planning"] = {"astra_calls": 0, "reports": {}, "final_token": None}
        planning.charge(state, "plan_review")
        planning.charge(state, "plan_finalize")
        with self.assertRaisesRegex(planning.s.Paused, "2/2 plan-review calls used"):
            planning.charge(state, "plan_finalize")

    def test_v2_roles_routes_and_context_engine_are_consistent(self):
        settings = {
            "engine": "opencode",
            "joint_planning": True,
            "planning_flow": "v2",
            "roles": {
                "requirements": {"engine": "opencode", "model": "fixture/requirements-model"},
                "glm": {"engine": "opencode", "model": "fixture/technical-model"},
                "plan_reviewer": {"engine": "opencode", "model": "fixture/independent-reviewer"},
            },
        }
        state = {"task": "Task", "workspace": str(Path.cwd()), "settings": settings}
        expected = {
            "requirements": "requirements",
            "plan": "glm",
            "plan_revise": "glm",
            "plan_review": "plan_reviewer",
            "plan_finalize": "plan_reviewer",
        }
        for stage, role in expected.items():
            with self.subTest(stage=stage):
                self.assertTrue(planning.is_planning(state, stage))
                self.assertEqual(role, planning.role_for(state, stage))
                self.assertEqual(role, planning.route_for(state, stage))
                with patch.object(support, "snapshot", return_value={"revision": "fixture", "head": "fixture"}):
                    packet = json.loads(
                        stage_context.context_packet(state, stage, Path("state.json"))[0].split(
                            "CURRENT HANDOFF DATA\n", 1
                        )[1]
                    )
                self.assertEqual(
                    planning.engine_for(settings, planning.route_for(state, stage)), packet["execution_engine"]
                )

    def test_v2_preserves_independent_requirements_planner_and_reviewer_routes(self):
        settings = {
            "joint_planning": True,
            "planning_flow": "v2",
            "roles": {
                "requirements": {"engine": "opencode", "model": "requirements-model"},
                "glm": {"engine": "opencode", "model": "technical-model"},
                "plan_reviewer": {"engine": "opencode", "model": "reviewer-model"},
            },
        }
        state = {"settings": settings}
        self.assertEqual("requirements", planning.route_for(state, "requirements"))
        self.assertEqual("glm", planning.route_for(state, "plan"))
        self.assertEqual("plan_reviewer", planning.route_for(state, "plan_review"))
        self.assertEqual("requirements-model", settings["roles"]["requirements"]["model"])
        self.assertEqual("technical-model", settings["roles"]["glm"]["model"])
        self.assertEqual("reviewer-model", settings["roles"]["plan_reviewer"]["model"])

    def test_v2_stages_use_fresh_read_only_routes_and_planning_metadata(self):
        evidence = Path.cwd() / ".autocode" / "evidence" / "planning-flow-v2-workspaces"
        evidence.mkdir(parents=True, exist_ok=True)
        root = Path(tempfile.mkdtemp(prefix="m3-", dir=evidence))
        self.addCleanup(shutil.rmtree, root, True)
        run = root / "run"
        run.mkdir()
        schema = run / "schema.json"
        schema.write_text(json.dumps({"type": "object", "properties": {}}))
        settings = {
            "engine": "opencode",
            "joint_planning": True,
            "planning_flow": "v2",
            "roles": {
                "requirements": {"engine": "opencode", "model": "fixture/requirements-model"},
                "glm": {"engine": "opencode", "model": "fixture/technical-model"},
                "plan_reviewer": {"engine": "opencode", "model": "fixture/reviewer-model"},
            },
        }
        state = {
            "settings": settings,
            "iteration": 1,
            "stages": [],
            "sessions": {role: "saved-" + role for role in settings["roles"]},
            "planning_artifacts": {},
        }
        for previous in ("requirements", "plan", "plan_review", "plan_revise"):
            artifact_path = "planning/" + previous + ".json"
            delta_path = "planning/" + previous + ".delta.json"
            for relative in (artifact_path, delta_path):
                path = run / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}\n")
            state["planning_artifacts"][previous] = {
                "artifact": {
                    "path": artifact_path,
                    "sha256": hashlib.sha256((run / artifact_path).read_bytes()).hexdigest(),
                },
                "delta": {"path": delta_path, "sha256": hashlib.sha256((run / delta_path).read_bytes()).hexdigest()},
            }
        for stage, role in planning.V2_STAGE_ROLES.items():
            with self.subTest(stage=stage):
                state["next_stage"] = stage
                value, record = runner.run_role(
                    role=role,
                    prompt="fixture",
                    sandbox="read-only",
                    workspace=root,
                    run_dir=run,
                    state=state,
                    schema=schema,
                    model=settings["roles"][role]["model"],
                    allow_write=False,
                    dry_run=True,
                )
                self.assertEqual({"status": "DRY_RUN"}, value)
                self.assertTrue(record["planning"])
                self.assertIsNone(record["expected_session"])
                self.assertEqual(
                    settings["roles"][role]["model"], record["command"][record["command"].index("--model") + 1]
                )

    def test_v2_predecessor_failures_stop_before_the_provider_launch(self):
        settings = {
            "engine": "opencode",
            "joint_planning": True,
            "planning_flow": "v2",
            "roles": {
                "requirements": {"engine": "opencode", "model": "fixture/requirements-model"},
                "glm": {"engine": "opencode", "model": "fixture/technical-model"},
                "plan_reviewer": {"engine": "opencode", "model": "fixture/reviewer-model"},
            },
        }
        state = {"settings": settings, "next_stage": "plan", "iteration": 1, "sessions": {}, "stages": []}
        with patch.object(runner.opencode, "launch", side_effect=AssertionError("provider launch")):
            with self.assertRaises(support.Paused) as error:
                planning.prepare(
                    state,
                    "plan",
                    Path.cwd() / ".autocode" / "evidence" / "state.json",
                    Path.cwd() / "tools" / "autocode-schemas",
                )
        self.assertEqual("PAUSED_INVALID_PREDECESSOR", error.exception.status)

    def test_v2_reviewer_route_requires_the_independently_configured_role(self):
        settings = {"joint_planning": True, "planning_flow": "v2", "roles": {"requirements": {}, "glm": {}}}
        for stage in ("plan_review", "plan_finalize"):
            with self.assertRaises(support.Paused) as error:
                planning.route_for({"settings": settings}, stage)
            self.assertEqual("PAUSED_PLANNING_ROUTE", error.exception.status)
        settings["roles"]["plan_reviewer"] = {"engine": "opencode", "model": "reviewer-model"}
        self.assertEqual("plan_reviewer", planning.route_for({"settings": settings}, "plan_review"))

    def test_v2_predecessor_gate_prevents_a_provider_attempt(self):
        settings = {
            "engine": "opencode",
            "joint_planning": True,
            "planning_flow": "v2",
            "roles": {
                "requirements": {"engine": "opencode", "model": "fixture/requirements-model"},
                "glm": {"engine": "opencode", "model": "fixture/technical-model"},
                "plan_reviewer": {"engine": "opencode", "model": "fixture/reviewer-model"},
            },
        }
        state = {"settings": settings, "next_stage": "plan", "iteration": 1, "sessions": {}, "stages": []}
        with self.assertRaises(support.Paused) as error:
            planning.prepare(
                state,
                "plan",
                Path.cwd() / ".autocode" / "evidence" / "state.json",
                Path.cwd() / "tools" / "autocode-schemas",
            )
        self.assertEqual("PAUSED_INVALID_PREDECESSOR", error.exception.status)
        self.assertEqual([], state["stages"])

    def test_v2_stage_names_do_not_overlap_figma_or_legacy_runner_names(self):
        state = self.state()
        figma_stages = {
            "requirements_planner",
            "plan_reviewer",
            "requirements_revision",
            "plan_finalizer",
            "builder",
            "validator",
        }
        runner_stages = set(planning.STAGES) | {
            "astra_plan",
            "astra_review",
            "astra_checkpoint",
            "terra",
            "sol",
            "completion",
        }
        self.assertFalse(set(planning.V2_STAGES) & figma_stages)
        self.assertFalse(set(planning.V2_STAGES) & runner_stages)
        for stage in figma_stages:
            with self.subTest(stage=stage):
                self.assertFalse(planning.is_planning(state, stage))
        self.assertEqual("astra", planning.role_for(state, "astra_plan"))
