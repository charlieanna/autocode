import copy
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
import autocode_program_guidance as guidance
import autocode_resolver_human as human
import autocode_stage_context as stage_context
import autocode_support as support
import autocode_util as util
import autopilot
from goal_fixtures import body, seed_greeting_workspace


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

    def _cycle_state(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        workspace = Path(temp.name).resolve()
        seed_greeting_workspace(workspace)
        util.git(workspace, "init", "-q", timeout=10)
        util.git(workspace, "add", ".", timeout=10)
        util.git(
            workspace,
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "fixture",
            timeout=10,
        )
        run_dir = workspace / ".autocode" / "runs" / "cycle"
        run_dir.mkdir(parents=True)
        state = self.state()
        state.update(
            workspace=str(workspace),
            run_dir=str(run_dir),
            active_seconds=1788.5,
            stages=[],
            history=[],
            failure_history={"old": {"count": 2}},
        )
        state["settings"].update(
            limits={"max_seconds": 7200, "stage_timeout_seconds": 900, "idle_timeout_seconds": 300},
            roles={
                role: {"model": role, "reasoning_effort": "high"}
                for role in ("requirements", "glm", "plan_reviewer", "terra", "sol", "completion")
            },
        )
        goals.clarification_episode(state)
        return state

    def _cycle_contract(self):
        contract = body()
        command = "python3 -m unittest discover -s . -t ."
        contract["acceptance_criteria"][0]["verification_method"] = command
        contract["initial_task"] = {
            "objective": "Deliver and verify the greeting flow",
            "affected_paths": ["greet.py"],
            "kind": "implement",
            "milestone_id": "M1",
            "requirements": ["Print Hello, NAME for a nonempty name"],
            "acceptance_criteria": ["C1"],
            "validation_plan": [command],
        }
        return contract

    def _cycle_requirements(self, state, summary="clear"):
        value = requirements()
        value["acceptance_criteria"] = copy.deepcopy(self._cycle_contract()["acceptance_criteria"])
        autopilot.apply_planning(
            state,
            "requirements",
            {"requirements": value, "summary": summary},
            {"stage": "requirements", "output": "requirements.json"},
        )

    def _cycle_plan(self, state):
        autopilot.apply_planning(
            state,
            "plan",
            {"contract": self._cycle_contract(), "summary": "planned"},
            {"stage": "plan", "output": "plan.json"},
        )

    def _complete_cycle(self, state):
        self._cycle_requirements(state)
        self._cycle_plan(state)
        review = {"stage": "plan_review", "output": "review.json", "exit_code": 0}
        planning.charge(state, "plan_review", record=review)
        autopilot.apply_planning(state, "plan_review", {"summary": "reviewed", "concerns": []}, review)
        autopilot.apply_planning(
            state,
            "plan_revise",
            {"contract": self._cycle_contract(), "summary": "revised", "responses": []},
            {"stage": "plan_revise", "output": "revision.json"},
        )
        final_value = {"contract": self._cycle_contract(), "summary": "final", "decisions": []}
        final_path = Path(state["run_dir"]) / "final.json"
        support.atomic_json(final_path, final_value)
        final = {
            "stage": "plan_finalize",
            "output": str(final_path),
            "exit_code": 0,
            "source_revision": support.snapshot(Path(state["workspace"]))["revision"],
        }
        planning.charge(state, "plan_finalize", record=final)
        autopilot.apply_planning(state, "plan_finalize", final_value, final)
        state["stages"].append(final)
        state["history"].append(copy.deepcopy(final))
        human.evaluate(state)
        lifecycle.present(state)
        self.assertEqual("AWAITING_GOAL_APPROVAL", state["status"])
        self.assertEqual(2, state["planning"]["astra_calls"])

    def test_v2_first_cycle_requires_review_and_finalization_with_two_call_limit(self):
        state = self._cycle_state()
        self._complete_cycle(state)
        self.assertEqual([], state.get("planning_history", []))
        self.assertEqual(2, planning.review_call_limit(state))
        self.assertIn("requirements", state["planning"]["reports"])
        self.assertEqual(state["displayed_goal"], state["planning"]["final_token"])
        with self.assertRaisesRegex(planning.s.Paused, "2/2 plan-review calls used"):
            planning.charge(state, "plan_finalize")

    def test_v2_feedback_archives_complete_cycle_and_preserves_run_limits_usage(self):
        state = self._cycle_state()
        self._complete_cycle(state)
        previous = copy.deepcopy(state["planning"])
        preserved = {
            key: copy.deepcopy(state[key])
            for key in ("settings", "active_seconds", "stages", "history", "failure_history", "task")
        }
        goals.feedback(state, "Correct the technical transcription; keep the original scope")
        self.assertEqual(previous, state["planning"])
        self._cycle_requirements(state)
        fresh = copy.deepcopy(state["planning"])
        self._cycle_plan(state)
        # Exercise admission first: the old V2 path refuses the first new review with 2/2 used.
        planning.charge(state, "plan_review")
        planning.charge(state, "plan_finalize")
        self.assertEqual(previous, state["planning_history"][-1])
        self.assertEqual(0, fresh["astra_calls"])
        self.assertEqual(2, planning.review_call_limit(state))
        self.assertIsNone(fresh["final_token"])
        self.assertEqual("plan_review", state["next_stage"])
        self.assertEqual(["requirements"], list(fresh["reports"]))
        self.assertEqual(preserved, {key: state[key] for key in preserved})
        with self.assertRaisesRegex(planning.s.Paused, "2/2 plan-review calls used"):
            planning.charge(state, "plan_finalize")

    def test_v2_intervention_feedback_renews_cycle_only_after_accepted_requirements(self):
        state = self._cycle_state()
        self._complete_cycle(state)
        previous = copy.deepcopy(state["planning"])
        old_episode = state["clarification_episode"]["id"]
        token = state["requirements_artifact_token"]
        goals.apply_intervention_feedback(
            state,
            {
                "id": "fix",
                "text": "Preserve the exact canonical filenames",
                "observed_goal_token": state["displayed_goal"],
            },
            {"applied_at": "2026-10-14T00:00:00+00:00"},
        )
        self.assertNotEqual(old_episode, state["clarification_episode"]["id"])
        self.assertEqual("intervention-fix", state["clarification_episode"]["started_by"])
        self.assertEqual("requirements", state["next_stage"])
        self.assertEqual(previous, state["planning"])
        self._cycle_requirements(state)
        self.assertEqual(token, state["requirements_artifact_token"])
        self.assertEqual(previous, state["planning_history"][-1])
        self.assertEqual("intervention-fix", state["planning"]["v2_cycle"]["boundary"]["clarification_input"])
        self.assertEqual(0, state["planning"]["astra_calls"])
        self.assertEqual(2, planning.review_call_limit(state))

    def test_v2_program_guidance_renews_review_without_new_investigation_episode(self):
        state = self._cycle_state()
        self._complete_cycle(state)
        previous = copy.deepcopy(state["planning"])
        episode = copy.deepcopy(state["clarification_episode"])
        token = state["requirements_artifact_token"]
        event = guidance.apply(
            state, "Restore the exact canonical filenames; preserve scope and limits", state["displayed_goal"]
        )
        self.assertEqual(episode, state["clarification_episode"])
        self.assertEqual(previous, state["planning"])
        self._cycle_requirements(state)
        self.assertEqual(token, state["requirements_artifact_token"])
        self.assertEqual(previous, state["planning_history"][-1])
        self.assertEqual(event["id"], state["planning"]["v2_cycle"]["boundary"]["program_guidance"])
        self.assertEqual(0, state["planning"]["astra_calls"])
        self.assertEqual(2, planning.review_call_limit(state))

    def test_v2_report_retries_and_revisions_do_not_start_duplicate_cycles(self):
        state = self._cycle_state()
        self._cycle_requirements(state)
        self._cycle_plan(state)
        planning.charge(state, "plan_review")
        previous = copy.deepcopy(state["planning"])
        invalid = requirements()
        invalid["acceptance_criteria"] = []
        with self.assertRaisesRegex(ValueError, "stable ID, behavior and verification method"):
            autopilot.apply_planning(
                state, "requirements", {"requirements": invalid, "summary": "invalid"}, {"output": "invalid.json"}
            )
        self.assertEqual(previous, state["planning"])
        self._cycle_requirements(state, summary="same input, refreshed report")
        self._cycle_plan(state)
        self.assertEqual(1, state["planning"]["astra_calls"])
        self.assertEqual(previous["v2_cycle"], state["planning"]["v2_cycle"])
        self.assertEqual([], state.get("planning_history", []))
        autopilot.apply_planning(
            state, "plan_review", {"summary": "reviewed", "concerns": []}, {"output": "review.json"}
        )
        autopilot.apply_planning(
            state,
            "plan_revise",
            {"contract": self._cycle_contract(), "summary": "revised", "responses": []},
            {"output": "revision.json"},
        )
        planning.charge(state, "plan_finalize")
        with self.assertRaisesRegex(planning.s.Paused, "2/2 plan-review calls used"):
            planning.charge(state, "plan_review")
        self.assertEqual([], state.get("planning_history", []))

    def test_v2_lazy_initial_episode_cannot_renew_review_capacity(self):
        state = self.state()
        self._cycle_requirements(state)
        self._cycle_plan(state)
        planning.charge(state, "plan_review")
        previous = copy.deepcopy(state["planning"]["v2_cycle"])
        goals.clarification_episode(state)
        self.assertEqual("initial_task", state["clarification_episode"]["started_by"])
        self._cycle_requirements(state, summary="same initial intent")
        self.assertEqual(1, state["planning"]["astra_calls"])
        self.assertEqual(previous, state["planning"]["v2_cycle"])
        self.assertEqual([], state.get("planning_history", []))

    def test_v2_unmarked_spent_cycle_baselines_retry_without_free_review_capacity(self):
        state = self._cycle_state()
        self._complete_cycle(state)
        # Model the genuine pre-fix record shape, which has no cycle marker.
        state["planning"].pop("v2_cycle")
        state["planning"].update(review_call_limit=2, review_call_limit_origin="user_explicit")
        previous = copy.deepcopy(state["planning"])
        history = copy.deepcopy(state.get("planning_history", []))
        preserved = {
            key: copy.deepcopy(state[key])
            for key in ("settings", "active_seconds", "stages", "history", "failure_history", "task")
        }
        self._cycle_requirements(state, summary="retry the unchanged operator input")
        baseline = copy.deepcopy(state["planning"]["v2_cycle"])
        self.assertEqual(2, state["planning"]["astra_calls"])
        self.assertEqual(previous["reports"]["plan_review"], state["planning"]["reports"]["plan_review"])
        self.assertEqual(previous["reports"]["plan_finalize"], state["planning"]["reports"]["plan_finalize"])
        self.assertEqual(previous["review_charges"], state["planning"]["review_charges"])
        self.assertEqual(previous["final_token"], state["planning"]["final_token"])
        self.assertEqual("user_explicit", state["planning"]["review_call_limit_origin"])
        self._cycle_requirements(state, summary="another changed report, same operator input")
        self._cycle_plan(state)
        self.assertEqual(baseline, state["planning"]["v2_cycle"])
        self.assertEqual(history, state.get("planning_history", []))
        self.assertEqual(preserved, {key: state[key] for key in preserved})
        with self.assertRaisesRegex(planning.s.Paused, "2/2 plan-review calls used") as denied:
            planning.charge(state, "plan_review")
        # A later explicit feedback boundary may begin one genuinely new cycle.
        state["status"] = denied.exception.status
        goals.feedback(state, "Correct the canonical filename; keep scope and limits")
        before_new = copy.deepcopy(state["planning"])
        self._cycle_requirements(state)
        self.assertEqual(before_new, state["planning_history"][-1])
        self.assertEqual(0, state["planning"]["astra_calls"])
        self.assertEqual(2, planning.review_call_limit(state))
        self.assertEqual(preserved, {key: state[key] for key in preserved})

    def test_v2_existing_empty_cycle_keeps_zero_usage_without_duplicate_archival(self):
        state = self.state()
        state["planning"] = {"astra_calls": 0, "reports": {}, "final_token": None}
        self._cycle_requirements(state)
        self._cycle_plan(state)
        self.assertEqual(0, state["planning"]["astra_calls"])
        self.assertEqual(2, planning.review_call_limit(state))
        self.assertEqual([], state.get("planning_history", []))
        planning.charge(state, "plan_review")
        planning.charge(state, "plan_finalize")
        with self.assertRaisesRegex(planning.s.Paused, "2/2 plan-review calls used"):
            planning.charge(state, "plan_review")

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


def plan_body():
    value = body()
    value["initial_task"] = {
        "objective": "Deliver greeting",
        "affected_paths": ["greet.py"],
        "kind": "implement",
        "milestone_id": "M1",
        "requirements": ["Deliver CLI"],
        "acceptance_criteria": ["C1"],
        "validation_plan": ["Run tests"],
    }
    value["milestones"][0]["affected_paths"] = ["greet.py"]
    return value


class V2PlanningCycleTests(unittest.TestCase):
    """Accepted v2 Requirements begin a new review cycle only for a changed handoff after new input (#1252)."""

    CONCERN = {
        "id": "C1",
        "concern": "Test evidence is incomplete",
        "evidence_refs": ["tests/test_goals.py"],
        "requested_change": "Add a regression",
        "acceptance_test": "Focused suite passes",
        "blocking": True,
    }

    def state(self):
        return {
            "task_id": "task",
            "task": "Task",
            "settings": {"joint_planning": True, "planning_flow": "v2"},
            "answers": {},
            "user_events": [],
            "acceptance_criteria": [],
            "stages": [],
            "active_seconds": 120,
        }

    def admit(self, state, summary="Requirements ready"):
        autopilot.apply_planning(
            state, "requirements", {"requirements": requirements(), "summary": summary}, {"output": "requirements.json"}
        )

    def review(self, state, stage, output, **outcome):
        """Admit one Plan Reviewer call as the runner does, then record how its attempt ended."""
        row = {"stage": stage, "output": output}
        planning.charge(state, stage, row)
        state["stages"].append({**row, **outcome})
        return row

    def reviewed_cycle(self, state):
        """A cycle that spent its whole allowance: a timed-out review (given back), then two that reported."""
        self.admit(state)
        autopilot.apply_planning(state, "plan", {"contract": plan_body(), "summary": "Plan"}, {"output": "plan.json"})
        self.review(state, "plan_review", "review-timeout.json", timed_out=True)
        self.review(state, "plan_review", "review.json", exit_code=0)
        autopilot.apply_planning(
            state, "plan_review", {"summary": "Concern", "concerns": [self.CONCERN]}, {"output": "review.json"}
        )
        self.review(state, "plan_finalize", "final.json", exit_code=0)
        with self.assertRaisesRegex(planning.s.Paused, "2/2 plan-review calls used") as paused:
            self.review(state, "plan_finalize", "refused.json")
        state.update(status=paused.exception.status, next_stage="plan_finalize")

    def test_initial_requirements_begin_the_first_cycle(self):
        state = self.state()
        self.admit(state)
        self.assertEqual({"astra_calls", "reports", "final_token", "inputs"}, set(state["planning"]))
        self.assertEqual(
            (0, None, []), tuple(state["planning"][key] for key in ("astra_calls", "final_token", "inputs"))
        )
        self.assertEqual(["requirements"], list(state["planning"]["reports"]))
        self.assertNotIn("planning_history", state)
        self.assertEqual(2, planning.review_call_limit(state))

    def test_feedback_with_a_changed_handoff_begins_a_new_cycle_and_keeps_history(self):
        state = self.state()
        self.reviewed_cycle(state)
        spent = copy.deepcopy(state["planning"])
        attempts, runtime = copy.deepcopy(state["stages"]), state["active_seconds"]
        self.assertEqual(2, spent["astra_calls"])

        goals.feedback(state, "Print the greeting on its own line")
        self.assertEqual(spent, state["planning"], "Feedback alone renews nothing")
        self.admit(state, summary="Requirements with the greeting on its own line")

        self.assertEqual([spent], state["planning_history"])
        self.assertEqual({"requirements", "plan", "plan_review"}, set(state["planning_history"][-1]["reports"]))
        self.assertEqual(attempts, state["stages"])
        self.assertEqual(runtime, state["active_seconds"])
        fresh = state["planning"]
        self.assertEqual((0, None), (fresh["astra_calls"], fresh["final_token"]))
        self.assertEqual(["feedback:" + state["brief_feedback"][-1]["id"]], fresh["inputs"])
        self.assertEqual(["requirements"], list(fresh["reports"]))
        self.assertEqual(2, planning.review_call_limit(state))
        self.assertEqual({"joint_planning": True, "planning_flow": "v2"}, state["settings"])
        # The corrected plan still needs a fresh independent review and the normal final review.
        self.assertEqual("plan", state["next_stage"])
        autopilot.apply_planning(state, "plan", {"contract": plan_body(), "summary": "Plan"}, {"output": "plan-2.json"})
        self.assertEqual("plan_review", state["next_stage"])
        self.assertFalse(goals.approved(state))
        self.review(state, "plan_review", "review-2.json", exit_code=0)
        self.review(state, "plan_finalize", "final-2.json", exit_code=0)
        with self.assertRaisesRegex(planning.s.Paused, "2/2 plan-review calls used"):
            self.review(state, "plan_finalize", "refused-2.json")
        self.assertEqual(2, state["planning_history"][-1]["astra_calls"])

    def test_the_same_handoff_or_no_new_input_keeps_the_cycle_and_its_charges(self):
        state = self.state()
        self.reviewed_cycle(state)
        spent = copy.deepcopy(state["planning"])
        cases = (  # (case, feedback given first, the Requirements summary, whether the handoff is unchanged)
            ("the same handoff admitted again", None, "Requirements ready", True),
            ("a retried report with no new input", None, "Reworded requirements", False),
            ("feedback the handoff does not change", "Keep it as it is", "Reworded requirements", True),
        )
        for label, feedback, summary, unchanged in cases:
            with self.subTest(label):
                if feedback:
                    state["status"] = "PAUSED_PLANNING_BUDGET"
                    goals.feedback(state, feedback)
                before = state["requirements_artifact_token"]
                self.admit(state, summary=summary)
                self.assertEqual(unchanged, before == state["requirements_artifact_token"])
                self.assertNotIn("planning_history", state)
                self.assertEqual(2, state["planning"]["astra_calls"])
                self.assertEqual(spent["review_charges"], state["planning"]["review_charges"])
                self.assertEqual([], state["planning"]["inputs"])
        state["next_stage"] = "plan_review"
        with self.assertRaisesRegex(planning.s.Paused, "2/2 plan-review calls used"):
            self.review(state, "plan_review", "refused.json")

    def test_feedback_after_a_plan_that_skipped_requirements_begins_a_new_cycle(self):
        state = self.state()
        # A bug fix or a checked design hands over straight to the Planner: no Requirements handoff yet.
        state.update(status="RUNNING", phase="PLANNING", next_stage="plan")
        autopilot.apply_planning(state, "plan", {"contract": plan_body(), "summary": "Plan"}, {"output": "plan.json"})
        self.review(state, "plan_review", "review.json", exit_code=0)
        autopilot.apply_planning(
            state, "plan_review", {"summary": "Concern", "concerns": [self.CONCERN]}, {"output": "review.json"}
        )
        self.review(state, "plan_finalize", "final.json", exit_code=0)
        state["status"] = "PAUSED_PLANNING_BUDGET"

        goals.feedback(state, "Print the greeting on its own line")
        self.admit(state, summary="Requirements with the greeting on its own line")

        self.assertEqual(0, state["planning"]["astra_calls"], "The old cycle's charges must not refuse the new review")
        self.assertEqual([2], [cycle["astra_calls"] for cycle in state.get("planning_history", [])])
        self.assertEqual(["feedback:" + state["brief_feedback"][-1]["id"]], state["planning"]["inputs"])
        autopilot.apply_planning(state, "plan", {"contract": plan_body(), "summary": "Plan"}, {"output": "plan-2.json"})
        self.assertEqual("plan_review", state["next_stage"])
        self.review(state, "plan_review", "review-2.json", exit_code=0)

    def test_a_record_saved_before_the_rule_keeps_its_charges_until_new_input(self):
        state = self.state()
        self.reviewed_cycle(state)
        del state["planning"]["inputs"]
        state["answers"]["Q1"] = {"kind": "answer", "text": "Before the upgrade"}
        self.admit(state, summary="Admitted after the upgrade")
        self.assertNotIn("planning_history", state)
        self.assertEqual((2, ["answer:Q1"]), (state["planning"]["astra_calls"], state["planning"]["inputs"]))
        state.setdefault("program_guidance", []).append({"id": "guidance-1", "text": "Keep R1"})
        self.admit(state, summary="Requirements after the program's guidance")
        self.assertEqual(2, state["planning_history"][-1]["astra_calls"])
        self.assertEqual(
            (0, ["answer:Q1", "guidance:guidance-1"]), (state["planning"]["astra_calls"], state["planning"]["inputs"])
        )
