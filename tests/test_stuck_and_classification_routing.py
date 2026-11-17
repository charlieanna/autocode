"""Regression tests for Investigator guidance retention, Builder failure classification routing, and same-round Resolver continuation."""

from __future__ import annotations

import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode as runner
import autocode_builder_policy as builder_policy
import autocode_goals as goals
import autocode_run_actions as run_actions
import autocode_run_records as run_records
import autocode_stuck_job as stuck_job
import autocode_support as support
import autopilot
import goal_fixtures


class SharedCauseFInvestigatorGuidanceAndStageLessonsTests(unittest.TestCase):
    """Investigator guidance survives transient/abandoned holds and stage_lessons admits report-repair lessons."""

    def setUp(self):
        scratch = self.enterContext(tempfile.TemporaryDirectory(prefix="stage-lessons-"))
        self.root = Path(scratch).resolve()

    def test_interrupted_execution_stage_retains_stuck_investigation_and_stage_output_lessons(self):
        state = {
            "version": 3,
            "workspace": "/ws",
            "run_dir": "/ws/.autocode/runs/chubby",
            "task": "Build chubby",
            "task_id": "chubby-1",
            "status": "RUNNING",
            "phase": "EXECUTING",
            "next_stage": "tester",
            "iteration": 2,
            "settings": {"roles": {"tester": {"model": "m1"}}, "limits": {}},
            "stages": [
                {
                    "stage": "tester",
                    "iteration": 1,
                    "rejected": True,
                    "source_revision": "rev-1",
                }
            ],
            "history": [],
            "user_events": [],
        }
        goal_fixtures.approve_fixture(state, goals)
        state["current_task"] = {"id": "validate-all", "kind": "validate", "milestone_id": "M5"}
        binding = stuck_job.recovery_binding.binding(state, "tester")
        state["stuck_investigations"] = [
            {
                "identity": "tester:PAUSED_REPEATED_FAILURE",
                "stage": "tester",
                "status": "PAUSED_REPEATED_FAILURE",
                "outcome": "retried",
                "cause": "stage_output",
                "trigger": "rejected_output",
                "diagnosis": "Tester cited raw mutant-*.log paths in evidence_refs.",
                "guidance": "Cite only regression-proof summary.json and open_lines handles, never mutant-*.log.",
                "source_revision": "rev-1",
                **binding,
            }
        ]
        state["stuck_investigation"] = {
            "identity": "tester:PAUSED_REPEATED_FAILURE",
            "stage": "tester",
            "status": "PAUSED_REPEATED_FAILURE",
            "diagnosis": "Tester cited raw mutant-*.log paths in evidence_refs.",
            "guidance": "Cite only regression-proof summary.json and open_lines handles, never mutant-*.log.",
            "example": "mutant-0001.log rejected",
            "in_force": True,
            **binding,
        }

        # Provider uncertainty and explicit stage abandonment preserve in-force guidance until tester completes.
        for transient_pause in ("PAUSED_PROVIDER_UNCERTAIN", "PAUSED_STAGE_ABANDONED", "PAUSED_RATE_LIMIT"):
            stuck_job.retire(state, transient_pause)
            self.assertIn("stuck_investigation", state, transient_pause)
            self.assertIn("INVESTIGATOR GUIDANCE", stuck_job.guidance(state, "tester"))

        # Even if a pre-fix checkpoint already popped stuck_investigation during PAUSED_PROVIDER_UNCERTAIN
        # before tester ever succeeded, unsettled stage_output lessons still guard the resumed tester run!
        pre_fix = copy.deepcopy(state)
        pre_fix.pop("stuck_investigation", None)
        recovered_guidance = stuck_job.guidance(pre_fix, "tester")
        self.assertIn("EARLIER STAGE CORRECTIONS", recovered_guidance)
        self.assertIn("never mutant-*.log", recovered_guidance)

        # Once tester completes and settles, both in_force and non-planning stage_output guidance are spent.
        stuck_job.settle(state, "tester")
        self.assertNotIn("stuck_investigation", state)
        self.assertEqual("", stuck_job.guidance(state, "tester"))

        # Non-convergence terminal pauses still retire active in_force guidance as before.
        state["stuck_investigation"] = {
            "identity": "tester:PAUSED_REPEATED_FAILURE",
            "stage": "tester",
            "status": "PAUSED_REPEATED_FAILURE",
            "diagnosis": "Diag",
            "guidance": "Guide",
            "in_force": True,
            **binding,
        }
        stuck_job.retire(state, "PAUSED_REPEATED_FAILURE")
        self.assertNotIn("stuck_investigation", state)

    def test_stuck_job_stage_lessons_admits_unlaunchable_report_repair_lessons_while_excluding_budget(self):
        recovery_binding = stuck_job.recovery_binding

        state = {
            "version": 3,
            "workspace": str(self.root),
            "task": "Build chubby",
            "status": "PAUSED_REPEATED_FAILURE",
            "phase": "EXECUTING",
            "next_stage": "tester",
            "stop_reason": (
                "Tester's report repair could not launch 4 times on unchanged source; another identical attempt "
                "is not started. Provider-decorated repair prompt exceeds 262144 bytes; no request was launched."
            ),
            "settings": {"stuck_investigation": {"enabled": True, "max_calls": 3}, "roles": {}},
            "stages": [
                {
                    "stage": "tester_report_repair",
                    "original_stage": "tester",
                    "iteration": 1,
                    "finished_at": "2026-11-16T16:00:00+00:00",
                }
            ],
            "stuck_investigations": [],
        }
        saved_reason = state["stop_reason"]
        self.assertTrue(stuck_job.intercept(state, "PAUSED_REPEATED_FAILURE", saved_reason))
        self.assertEqual("rejected_output", state["stuck_investigations"][-1]["trigger"])

        # Pre-fix saved row with trigger='non_convergence' on report-repair repeated failure is also admitted.
        bound = recovery_binding.binding(state, "tester")
        state["stuck_investigations"] = [
            {
                "identity": stuck_job.identity("tester", "PAUSED_REPEATED_FAILURE"),
                "stage": "tester",
                "status": "PAUSED_REPEATED_FAILURE",
                "reason": saved_reason,
                "outcome": "retried",
                "cause": "stage_output",
                "trigger": "non_convergence",
                "diagnosis": "Large exception list overflowed repair prompt.",
                "guidance": "Emit short L<N> handles directly in the primary Tester report.",
                "finished_at": "2026-11-16T16:05:00+00:00",
                **bound,
            }
        ]
        lessons = stuck_job.stage_lessons(state, "tester")
        self.assertEqual(1, len(lessons))
        self.assertIn("short L<N> handles", lessons[0]["guidance"])

        # Planning budget non_convergence remains strictly excluded.
        budget_state = {
            "version": 3,
            "workspace": str(self.root),
            "task": "Build chubby",
            "status": "RUNNING",
            "phase": "PLANNING",
            "next_stage": "revise_plan",
            "settings": {"stuck_investigation": {"enabled": True, "max_calls": 3}, "roles": {}},
            "stages": [],
            "stuck_investigations": [
                {
                    "identity": stuck_job.identity("revise_plan", "PAUSED_PLANNING_BUDGET"),
                    "stage": "revise_plan",
                    "status": "PAUSED_PLANNING_BUDGET",
                    "reason": "Planning review call limit reached",
                    "outcome": "retried",
                    "cause": "stage_output",
                    "trigger": "non_convergence",
                    "diagnosis": "Too many review rounds.",
                    "guidance": "Converge faster.",
                    "finished_at": "2026-11-16T16:05:00+00:00",
                    **recovery_binding.binding(
                        {"workspace": str(self.root), "task": "Build chubby", "user_events": []}, "revise_plan"
                    ),
                }
            ],
        }
        self.assertEqual([], stuck_job.planning_lessons(budget_state))


class SharedCauseGBigtableChangedCodeClassificationTests(unittest.TestCase):
    """Builder-failure Investigator retry on changed_code_rework never self-deadlocks on failure_class='unknown'."""

    def setUp(self):
        scratch = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(scratch).resolve()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / "tablet.py").write_text("x = 1\n")
        subprocess.run(
            ["git", "-C", str(self.root), "-c", "user.name=T", "-c", "user.email=t@e.t", "add", "tablet.py"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.root), "-c", "user.name=T", "-c", "user.email=t@e.t", "commit", "-qm", "init"],
            check=True,
        )
        self.run = self.root / ".autocode" / "runs" / "bt-1"
        (self.run / "iterations" / "006").mkdir(parents=True)

    def test_changed_code_rework_unknown_retry_maps_to_execution_and_reconciles_saved_hold(self):
        snap = support.snapshot(self.root)
        val_out = self.run / "iterations" / "006" / "validator-01.json"
        val_ev = self.run / "iterations" / "006" / "validator-01.jsonl"
        inv_out = self.run / "iterations" / "006" / "stuck-investigation-01.json"
        val_out.write_text('{"verdict": "PASS", "findings": []}')
        val_ev.write_text('{"type": "turn.completed"}\n')
        state = {
            "version": 3,
            "workspace": str(self.root),
            "run_dir": str(self.run),
            "task": "Build bigtable",
            "task_id": "bt-1",
            "status": "RUNNING",
            "phase": "INVESTIGATING",
            "next_stage": stuck_job.STAGE,
            "iteration": 6,
            "next_action": "Changed product lines still need behavioral evidence.",
            "settings": {
                "engine": "opencode",
                "roles": {
                    "builder": {"model": "m-b", "reasoning_effort": "high"},
                    "tester": {"model": "m-t", "reasoning_effort": "high"},
                    "completion": {"model": "m-c", "reasoning_effort": "high"},
                    "task_planner": {"model": "m-p", "reasoning_effort": "high"},
                },
                "builder_retry": dict(builder_policy.DEFAULTS),
                "limits": {},
            },
            "stages": [
                {
                    "stage": "tester",
                    "role": "tester",
                    "iteration": 6,
                    "output": str(val_out),
                    "events": str(val_ev),
                    "source_revision": snap["revision"],
                    "task_id": "task-m2",
                    "exit_code": 0,
                }
            ],
            "history": [],
            "user_events": [],
        }
        goal_fixtures.approve_fixture(state, goals)
        state["current_task"] = {
            "id": "task-m2",
            "kind": "implement",
            "milestone_id": "M2",
            "affected_paths": ["tablet.py"],
        }
        assessment = {"status": "FAIL", "reason": "Changed product lines still need behavioral evidence: tablet.py:1"}
        ev = autopilot.builder_failure.evidence(
            state, state["stages"][0], diagnosis={"changed_code_evidence": assessment}
        )
        state["regression_proofs"] = [
            {
                "kind": "changed_code_rework",
                "action": "investigator",
                "task_id": "task-m2",
                "contract_hash": state["goal_contract"]["hash"],
                "contract_revision": state["goal_contract"]["revision"],
                "source_revision": snap["revision"],
                "review_output": str(val_out),
                "assessment": assessment,
            }
        ]
        state["stuck_investigation"] = {
            "identity": "builder-failure:" + ev["failure_id"],
            "mode": "builder_failure",
            "stage": "builder",
            "status": "PAUSED_BUILDER_CLASSIFICATION",
            "reason": assessment["reason"],
            "phase": "EXECUTING",
            "requested_at": "2026-11-16T15:11:00+00:00",
            "failure_evidence": copy.deepcopy(ev),
        }
        state["stuck_investigations"] = [
            {
                "identity": "builder-failure:" + ev["failure_id"],
                "stage": "builder",
                "status": "PAUSED_BUILDER_CLASSIFICATION",
                "outcome": "investigating",
                "trigger": "builder_failure",
            }
        ]
        inv_report = {
            "diagnosis": "Evidence coverage gap on tablet.py:1.",
            "cause": "stage_output",
            "guidance": "Add a unit test killing the mutant on tablet.py:1.",
            "recommendation": "retry",
            "user_question": "",
            "evidence_refs": [str(val_out)],
            "example": "Validator omitted L1.",
            "probe": "",
            "untestable": "Fixture test without sandbox probe.",
            "failure_class": "unknown",
            "failure_id": ev["failure_id"],
        }
        inv_out.write_text(json.dumps(inv_report))

        # 1. Prevention: stuck_job.apply normalizes changed_code_rework 'unknown' + 'retry' to 'execution'.
        live_state = copy.deepcopy(state)
        continuation = stuck_job.apply(live_state, inv_report, {"output": str(inv_out), "changed_files": []}, self.root)
        self.assertEqual("execution", continuation["diagnosis"]["failure_class"])
        self.assertEqual("execution", live_state["stuck_investigations"][-1]["failure_class"])
        action = autopilot.route_builder_failure(live_state, continuation["failure_evidence"], continuation["reason"])
        self.assertIn(action, ("retry", "escalate"))
        self.assertNotIn("builder_failure_hold", live_state)

        # Non-changed_code_rework empty-diff 'unknown' + 'retry' is rejected instead of silently deadlocking.
        non_cc_state = copy.deepcopy(state)
        non_cc_state["stuck_investigation"]["failure_evidence"]["diagnosis"] = None
        with self.assertRaisesRegex(ValueError, "concrete failure_class"):
            stuck_job.apply(non_cc_state, inv_report, {"output": str(inv_out), "changed_files": []}, self.root)

        # 2. Saved-checkpoint reconciliation (like bigtable f2332754):
        saved_state = copy.deepcopy(state)
        saved_state.pop("stuck_investigation")
        saved_state["stuck_investigations"][-1].update(
            outcome="classified",
            failure_class="unknown",
            failure_id=ev["failure_id"],
            cause="stage_output",
            guidance=inv_report["guidance"],
            diagnosis=inv_report["diagnosis"],
            evidence_refs=inv_report["evidence_refs"],
            output=str(inv_out),
            reason=assessment["reason"],
        )
        autopilot.builder_failure.hold(saved_state, ev, "PAUSED_BUILDER_CLASSIFICATION", assessment["reason"])
        run_records.write_json(self.run / "state.json", saved_state)
        with patch.object(runner.opencode, "check_models", return_value=None):
            args_r, parser = runner.cli_args.parse(
                None,
                ["--workspace", str(self.root), "--run-dir", str(self.run), "--resume-paused", "--no-chat"],
                runner.opencode.DEFAULT_MODELS,
            )
            self.assertIsNone(
                run_actions.handle(runner, args_r, parser, saved_state, self.run / "state.json", self.run, self.root)
            )
        self.assertEqual("RUNNING", saved_state["status"])
        self.assertEqual("builder", saved_state["next_stage"])
        self.assertNotIn("builder_failure_hold", saved_state)
        self.assertEqual("execution", saved_state["stuck_investigations"][-1]["failure_class"])
        self.assertIsNotNone(saved_state["regression_proofs"][-1].get("investigation"))
        self.assertIn("Investigator: Add a unit test", saved_state["next_action"])


class SharedCauseJBuilderFailureAdvisoryRecommendationTests(unittest.TestCase):
    """builder_failure Investigator honors prompt.md:1 (advisory recommendation; failure_class routes) (#55)."""

    def setUp(self):
        scratch = self.enterContext(tempfile.TemporaryDirectory(prefix="mr-plan-reassess-"))
        self.root = Path(scratch).resolve()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / "server.py").write_text("x = 1\n")
        subprocess.run(
            ["git", "-C", str(self.root), "-c", "user.name=t", "-c", "user.email=t@e", "add", "server.py"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.root), "-c", "user.name=t", "-c", "user.email=t@e", "commit", "-qm", "init"],
            check=True,
        )
        self.run = self.root / ".autocode" / "runs" / "mr-1"
        (self.run / "iterations" / "008").mkdir(parents=True)

    def _fixture(self) -> tuple[dict, dict, Path, Path]:
        state = {
            "version": 3,
            "workspace": str(self.root),
            "run_dir": str(self.run),
            "task": "Build mapreduce",
            "task_id": "t-mr",
            "status": "RUNNING",
            "phase": "EXECUTING",
            "next_stage": "builder",
            "iteration": 8,
            "settings": {
                "engine": "opencode",
                "builder_retry": dict(builder_policy.DEFAULTS),
                "milestone_checkpoints": {"enabled": True, "max_replans": 1, "stalled_reviews": 3},
                "stuck_investigation": {"enabled": True, "max_calls": 3},
                "roles": {
                    role: {"engine": "opencode", "model": "openai/gpt-6-sol", "reasoning_effort": "high"}
                    for role in ("task_planner", "planner", "plan_reviewer", "builder", "tester", "resolver")
                },
                "limits": {},
            },
            "stages": [],
            "history": [],
            "user_events": [],
        }
        goal_fixtures.approve_fixture(state, goals)
        state["goal_contract"]["body"]["milestones"] = [
            {
                "id": "M4",
                "objective": "Deliver HTTP service",
                "acceptance_criteria": ["C1"],
                "affected_paths": ["server.py", "mapreduce/coordinator.py"],
                "depends_on": [],
            }
        ]
        state["goal_contract"]["hash"] = support.digest(
            {k: state["goal_contract"][k] for k in ("task_id", "revision", "body")}
        )
        state["goal_contract"]["approval_event"]["token"] = goals.token(state["goal_contract"])
        snap = runner.source_scope.snapshot(self.root, state, base_snapshot=support.snapshot)
        state["current_task"] = {
            "id": "task-m4",
            "kind": "implement",
            "milestone_id": "M4",
            "objective": "Deliver M4 HTTP service",
            "affected_paths": ["server.py"],
            "acceptance_criteria": ["C1"],
            "contract_hash": state["goal_contract"]["hash"],
            "contract_revision": state["goal_contract"]["revision"],
            "source_revision": snap["revision"],
        }
        m_progress_key = f"{state['goal_contract']['hash']}:M4"
        state["milestone_progress"] = {
            m_progress_key: {
                "id": "M4",
                "contract_hash": state["goal_contract"]["hash"],
                "objective": "Deliver HTTP service",
                "affected_paths": ["server.py", "mapreduce/coordinator.py"],
                "acceptance_criteria": ["C1"],
                "depends_on": [],
                "accepted": False,
                "replans": 0,
                "reviews": [],
                "reviews_without_progress": 0,
            }
        }
        state["resolution_request"] = {
            "contract_hash": state["goal_contract"]["hash"],
            "source_output": str(self.run / "iterations" / "007" / "completion-review-01.json"),
        }
        rev_out = self.run / "iterations" / "008" / "completion-review-01.json"
        rev_out.write_text('{"status": "REWORK"}\n')
        rec = {
            "stage": "review_completion",
            "role": "completion",
            "iteration": 7,
            "output": str(rev_out),
            "task_id": "task-m4",
            "source_revision": snap["revision"],
            "exit_code": 0,
        }
        state["stages"].append(rec)
        ev = autopilot.builder_failure.evidence(state, rec)
        inv_out = self.run / "iterations" / "008" / "stuck-inv-01.json"
        return state, ev, rev_out, inv_out

    def test_plan_role_disagreement_with_advisory_pause_routes_to_review_completion_and_reconciles_saved_hold(self):
        state, ev, rev_out, inv_out = self._fixture()
        m_progress_key = f"{state['goal_contract']['hash']}:M4"
        self.assertTrue(
            autopilot.builder_failure.queue(state, ev, "Builder made no edits due to narrow paths", enabled=True)
        )
        inv_report = {
            "diagnosis": "Task affected_paths omitted mapreduce/coordinator.py; Plan Reviewer must extend paths.",
            "cause": "role_disagreement",
            "guidance": "",
            "recommendation": "pause",
            "user_question": "Have Plan Reviewer re-issue task with extended affected_paths.",
            "evidence_refs": [str(rev_out)],
            "example": "Completion review required coordinator.py while task only listed server.py.",
            "probe": "",
            "untestable": "Fixture test without sandbox probe.",
            "failure_class": "plan",
            "failure_id": ev["failure_id"],
        }
        inv_out.write_text(json.dumps(inv_report))

        # 1. Live prevention: recommendation='pause' with failure_class='plan' and cause!='needs_user' routes to replan!
        live_state = copy.deepcopy(state)
        continuation = stuck_job.apply(live_state, inv_report, {"output": str(inv_out), "changed_files": []}, self.root)
        self.assertIsNotNone(continuation)
        self.assertNotIn("builder_failure_hold", live_state)
        self.assertEqual("classified", live_state["stuck_investigations"][-1]["outcome"])
        self.assertEqual(inv_report["diagnosis"], continuation["diagnosis"]["guidance"])
        action = autopilot.route_builder_failure(live_state, continuation["failure_evidence"], continuation["reason"])
        self.assertEqual("replan", action)
        self.assertEqual("RUNNING", live_state["status"])
        self.assertEqual("REVIEWING", live_state["phase"])
        self.assertEqual("review_completion", live_state["next_stage"])
        self.assertNotIn("resolution_request", live_state)
        self.assertIsNotNone(live_state["milestone_progress"][m_progress_key].get("builder_reassessment"))

        # Genuine needs_user or unknown + pause still hold at PAUSED_BUILDER_CLASSIFICATION!
        for override in (
            {"cause": "needs_user", "failure_class": "plan"},
            {"cause": "other", "failure_class": "unknown"},
        ):
            with self.subTest(override=override):
                held_case = copy.deepcopy(state)
                self.assertIsNone(
                    stuck_job.apply(
                        held_case,
                        {**inv_report, **override},
                        {"output": str(inv_out), "changed_files": []},
                        self.root,
                    )
                )
                self.assertEqual("PAUSED_BUILDER_CLASSIFICATION", held_case["status"])
                self.assertIn("builder_failure_hold", held_case)

        # 2. Pre-fix saved-checkpoint reconciliation (like mapreduce 250e6e56):
        saved_state = copy.deepcopy(state)
        saved_state.pop("stuck_investigation")
        saved_state["stuck_investigations"][-1].update(
            outcome="paused",
            failure_class="plan",
            failure_id=ev["failure_id"],
            cause="role_disagreement",
            diagnosis=inv_report["diagnosis"],
            guidance="",
            user_question=inv_report["user_question"],
            evidence_refs=inv_report["evidence_refs"],
            output=str(inv_out),
            reason="Builder made no edits due to narrow paths",
        )
        autopilot.builder_failure.hold(
            saved_state, ev, "PAUSED_BUILDER_CLASSIFICATION", "Builder made no edits due to narrow paths"
        )
        # Genuine needs_user saved hold refuses reconciliation on --resume-paused:
        needs_user_saved = copy.deepcopy(saved_state)
        needs_user_saved["stuck_investigations"][-1]["cause"] = "needs_user"
        self.assertFalse(
            autopilot.builder_failure.reconcile_routable_hold(
                needs_user_saved, self.root, route_failure=autopilot.route_builder_failure
            )
        )

        run_records.write_json(self.run / "state.json", saved_state)
        with patch.object(runner.opencode, "check_models", return_value=None):
            args_r, parser = runner.cli_args.parse(
                None,
                ["--workspace", str(self.root), "--run-dir", str(self.run), "--resume-paused", "--no-chat"],
                runner.opencode.DEFAULT_MODELS,
            )
            self.assertIsNone(
                run_actions.handle(runner, args_r, parser, saved_state, self.run / "state.json", self.run, self.root)
            )
        self.assertEqual("RUNNING", saved_state["status"])
        self.assertEqual("REVIEWING", saved_state["phase"])
        self.assertEqual("review_completion", saved_state["next_stage"])
        self.assertNotIn("builder_failure_hold", saved_state)
        self.assertEqual("classified", saved_state["stuck_investigations"][-1]["outcome"])
        self.assertIsNotNone(saved_state["milestone_progress"][m_progress_key].get("builder_reassessment"))


class SharedCauseNGfsResolverBuilderClassificationTests(unittest.TestCase):
    """Task 3: non-execution classifications never starve execution lane; same-source re-resolution reconciles."""

    def setUp(self):
        scratch = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(scratch).resolve()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / "server.py").write_text("from gfs.server import serve\n")
        (self.root / "gfs").mkdir()
        (self.root / "gfs" / "__init__.py").write_text("")
        (self.root / "gfs" / "server.py").write_text(
            "class GfsService:\n    def read_file(self, path: str) -> bytes:\n        return b'AAAADDDD'\n"
        )
        (self.root / "tests").mkdir()
        (self.root / "tests" / "__init__.py").write_text("")
        (self.root / "tests" / "test_service_core.py").write_text("import unittest\n")
        subprocess.run(["git", "-C", str(self.root), "add", "."], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(self.root),
                "-c",
                "user.name=Test",
                "-c",
                "user.email=t@e",
                "commit",
                "-q",
                "-m",
                "init",
            ],
            check=True,
        )
        self.run = self.root / ".autocode" / "runs" / "gfs-1"
        for it in ("013", "014", "015", "016", "017", "018"):
            (self.run / "iterations" / it).mkdir(parents=True, exist_ok=True)
        (self.run / "evidence").mkdir(parents=True, exist_ok=True)

    def _gfs_state(self):
        state = {
            "version": 3,
            "task_id": "task-root-gfs",
            "run_dir": str(self.run),
            "workspace": str(self.root),
            "task": "Build GFS service",
            "status": "RUNNING",
            "phase": "RESOLVING",
            "next_stage": "resolve",
            "next_action": "Validate M3 HTTP service",
            "iteration": 17,
            "settings": {
                "engine": "opencode",
                "provider": "kilocode",
                "builder_retry": {
                    "enabled": True,
                    "ordinary_retries": 1,
                    "strong_model": "openai/gpt-6-sol",
                    "strong_reasoning_effort": "xhigh",
                    "strong_origin": "runner",
                    "checker_model": "zai-coding-plan/glm-5.3",
                },
                "milestone_checkpoints": {"enabled": True, "max_replans": 1, "stalled_reviews": 3},
                "stuck_investigation": {"enabled": True, "max_calls": 3},
                "roles": {
                    "task_planner": {"engine": "opencode", "model": "openai/gpt-6-astra", "reasoning_effort": "high"},
                    "planner": {"engine": "opencode", "model": "zai-coding-plan/glm-5.3", "reasoning_effort": "high"},
                    "plan_reviewer": {"engine": "opencode", "model": "openai/gpt-6-sol", "reasoning_effort": "high"},
                    "builder": {"engine": "opencode", "model": "openai/gpt-6-sol", "reasoning_effort": "xhigh"},
                    "tester": {"engine": "opencode", "model": "zai-coding-plan/glm-5.3", "reasoning_effort": "high"},
                    "completion": {
                        "engine": "opencode",
                        "model": "zai-coding-plan/glm-5.3",
                        "reasoning_effort": "high",
                    },
                    "resolver": {"engine": "opencode", "model": "openai/gpt-6-astra", "reasoning_effort": "high"},
                },
                "limits": {},
            },
            "stages": [],
            "history": [],
            "user_events": [],
        }
        goal_fixtures.approve_fixture(state, goals)
        state["goal_contract"]["body"]["milestones"] = [
            {
                "id": "M3",
                "objective": "Deliver GFS HTTP service",
                "acceptance_criteria": ["C1"],
                "affected_paths": ["server.py", "gfs/server.py", "tests/test_service_core.py"],
                "depends_on": [],
            }
        ]
        state["goal_contract"]["hash"] = support.digest(
            {k: state["goal_contract"][k] for k in ("task_id", "revision", "body")}
        )
        state["goal_contract"]["approval_event"]["token"] = goals.token(state["goal_contract"])
        state["acceptance_criteria"] = copy.deepcopy(state["goal_contract"]["body"]["acceptance_criteria"])
        state["criteria_revision"] = support.digest(support.criteria_definition(state["acceptance_criteria"]))
        snap = runner.source_scope.snapshot(self.root, state, base_snapshot=support.snapshot)
        state["current_task"] = {
            "id": "task-validate-m3",
            "kind": "validate",
            "milestone_id": "M3",
            "objective": "Validate M3 HTTP service",
            "requirements": ["Independently validate C1 on current artifact."],
            "validation_plan": ["python3 -m unittest discover -s tests -t ."],
            "affected_paths": ["server.py", "gfs/server.py", "tests/test_service_core.py"],
            "acceptance_criteria": ["C1"],
            "contract_hash": state["goal_contract"]["hash"],
            "contract_revision": state["goal_contract"]["revision"],
            "source_revision": snap["revision"],
        }
        m_progress_key = f"{state['goal_contract']['hash']}:M3"
        state["milestone_progress"] = {
            m_progress_key: {
                "id": "M3",
                "contract_hash": state["goal_contract"]["hash"],
                "objective": "Deliver GFS HTTP service",
                "affected_paths": ["server.py", "gfs/server.py", "tests/test_service_core.py"],
                "acceptance_criteria": ["C1"],
                "depends_on": [],
                "accepted": False,
                "build_started": True,
                "replans": 1,
                "reviews": [],
                "reviews_without_progress": 0,
                "seconds": 0.0,
            }
        }
        return state, snap

    def test_non_execution_classifications_do_not_exhaust_execution_classification_limit_and_unqueued_hold_reconciles(
        self,
    ):
        state, snap = self._gfs_state()
        m_key = builder_policy.key(state)
        self.assertEqual(3, builder_policy.classification_limit(state))
        state["stuck_investigations"] = [
            {
                "identity": "builder-failure:exec-1",
                "trigger": "builder_failure",
                "milestone_key": m_key,
                "outcome": "classified",
                "failure_class": "execution",
                "cause": "other",
            },
            {
                "identity": "builder-failure:plan-1",
                "trigger": "builder_failure",
                "milestone_key": m_key,
                "outcome": "classified",
                "failure_class": "plan",
                "cause": "other",
            },
            {
                "identity": "builder-failure:exec-2",
                "trigger": "builder_failure",
                "milestone_key": m_key,
                "outcome": "classified",
                "failure_class": "execution",
                "cause": "other",
            },
        ]
        # Because plan-1 is a non-execution classification, execution slot #3 remains open (limit == 4):
        self.assertEqual(3, len(autopilot.builder_failure.classified(state)))
        self.assertEqual(4, builder_policy.classification_limit(state))

        # A fresh Builder attempt on a new source revision can still queue classification #4 AND
        # a pre-fix unqueued PAUSED_BUILDER_CLASSIFICATION hold reconciles into investigate_stuck!
        rev18 = self.run / "iterations" / "018" / "completion-review-01.json"
        rev18.write_text('{"status": "REWORK"}\n')
        rec18 = {
            "stage": "review_completion",
            "role": "completion",
            "iteration": 18,
            "output": str(rev18),
            "task_id": "task-validate-m3",
            "source_revision": snap["revision"],
            "exit_code": 0,
        }
        state["stages"].append(rec18)
        ev18 = autopilot.builder_failure.evidence(state, rec18)
        autopilot.builder_failure.hold(
            state,
            ev18,
            "PAUSED_BUILDER_CLASSIFICATION",
            "Read race remains; failure classification requires reconciled evidence or operator action",
        )
        self.assertEqual("PAUSED_BUILDER_CLASSIFICATION", state["status"])
        self.assertTrue(
            autopilot.builder_failure.reconcile_routable_hold(
                state,
                self.root,
                route_failure=autopilot.route_builder_failure,
                enabled=True,
            )
        )
        self.assertEqual("RUNNING", state["status"])
        self.assertEqual("INVESTIGATING", state["phase"])
        self.assertEqual("investigate_stuck", state["next_stage"])
        self.assertNotIn("builder_failure_hold", state)
        self.assertEqual("builder-failure:" + ev18["failure_id"], state["stuck_investigation"]["identity"])

    def test_gfs_same_unretried_builder_round_reuses_diagnosis_attests_probe_and_reconciles_saved_hold(self):
        import autocode_resolver_recovery as rr

        state, snap = self._gfs_state()
        m_key = builder_policy.key(state)
        probe_cmd = "python3 -m unittest tests.test_service_core"
        val16_out = self.run / "iterations" / "016" / "validator-01.json"
        val16_events = self.run / "iterations" / "016" / "validator-01.jsonl"
        val16_events.write_text(
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "id": "cmd-1",
                        "type": "command_execution",
                        "command": probe_cmd,
                        "exit_code": 0,
                        "aggregated_output": "REPRODUCED: AAAADDDD\n",
                    },
                }
            )
            + "\n"
        )
        val16_body = {
            "verdict": "FAIL",
            "summary": "Cross-chunk read race reproduced",
            "checks": [
                {
                    "command": probe_cmd,
                    "exit_code": 0,
                    "summary": "REPRODUCED: AAAADDDD",
                    "evidence_ref": "event:cmd-1",
                }
            ],
            "criterion_results": [{"id": "C1", "status": "FAIL", "evidence": "Race reproduced", "evidence_refs": []}],
            "findings": [
                {
                    "id": "",
                    "blocking": True,
                    "severity": "high",
                    "finding": "Concurrent rewrite lets a multi-chunk read return bytes from two file generations",
                    "evidence": "event:cmd-1",
                }
            ],
            "user_request": {
                "kind": "none",
                "discovered": "",
                "impact": "",
                "decision_needed": "",
                "options": [],
                "proposed_delta": "",
            },
            "changed_code_exceptions": [],
        }
        val16_out.write_text(json.dumps(val16_body))
        state["validation"] = {
            **val16_body,
            "output": str(val16_out),
            "task_id": "task-validate-m3",
            "contract_hash": state["goal_contract"]["hash"],
            "contract_revision": state["goal_contract"]["revision"],
            "source_revision": snap["revision"],
            "evidence_hashes": {
                str(val16_out): support.file_hash(val16_out),
                str(val16_events): support.file_hash(val16_events),
            },
        }
        rev16_out = self.run / "iterations" / "016" / "completion-review-01.json"
        rev16_out.write_text('{"status": "REWORK"}\n')
        inv17_out = self.run / "iterations" / "017" / "stuck-investigation-01.json"
        inv17_report = {
            "cause": "other",
            "diagnosis": "read_file reads chunks without holding the mutation lock.",
            "guidance": "Acquire the shared lock around stat_file and chunk reads.",
            "example": "Concurrent rewrite yields AAAADDDD.",
            "recommendation": "retry",
            "user_question": "",
            "failure_class": "execution",
            "failure_id": "prior-exec-id",
            "evidence_refs": [str(rev16_out)],
        }
        inv17_out.write_text(json.dumps(inv17_report))
        rev17_decision = {
            "contract_revision": state["goal_contract"]["revision"],
            "contract_hash": state["goal_contract"]["hash"],
            "task_id": "task-validate-m3",
            "status": "REWORK",
            "blocker": "",
            "next_objective": "Serialize read_file under the mutation lock in M3.",
            "plan": ["Serialize read_file in gfs/server.py and add regression test."],
            "affected_paths": ["server.py", "gfs/server.py", "tests/test_service_core.py"],
            "acceptance_criteria": [
                {
                    "id": "C1",
                    "criterion": state["acceptance_criteria"][0]["criterion"],
                    "status": "blocked",
                    "evidence": "Race",
                }
            ],
            "findings": [
                {
                    "id": "",
                    "blocking": True,
                    "severity": "high",
                    "finding": "Concurrent rewrite lets a multi-chunk read return bytes from two file generations",
                    "evidence": str(val16_out),
                }
            ],
            "finding_dispositions": [],
            "deferred_backlog": [],
            "agreed_limitations": [],
            "evidence": [str(val16_out)],
            "recovery_change": None,
            "source_verdict": {"verdict": "FAIL", "summary": "Race open", "uncovered": ["Byte integrity"]},
            "user_request": {
                "kind": "none",
                "discovered": "",
                "impact": "",
                "decision_needed": "",
                "options": [],
                "proposed_delta": "",
            },
        }
        rev17_out = self.run / "iterations" / "017" / "completion-review-01.json"
        rev17_events = self.run / "iterations" / "017" / "completion-review-01.jsonl"
        rev17_out.write_text(json.dumps(rev17_decision))
        rev17_events.write_text('{"type": "done"}\n')
        val16_rec = {
            "stage": "tester",
            "role": "tester",
            "iteration": 16,
            "output": str(val16_out),
            "events": str(val16_events),
            "task_id": "task-validate-m3",
            "contract_hash": state["goal_contract"]["hash"],
            "contract_revision": state["goal_contract"]["revision"],
            "source_revision": snap["revision"],
            "exit_code": 0,
        }
        rev17_rec = {
            "stage": "review_completion",
            "role": "completion",
            "iteration": 17,
            "output": str(rev17_out),
            "events": str(rev17_events),
            "task_id": "task-validate-m3",
            "contract_hash": state["goal_contract"]["hash"],
            "contract_revision": state["goal_contract"]["revision"],
            "source_revision": snap["revision"],
            "exit_code": 0,
        }
        state["stages"] = [
            {
                "stage": "builder",
                "role": "builder",
                "iteration": 15,
                "output": str(self.run / "iterations" / "015" / "builder-01.json"),
                "source_revision": snap["revision"],
                "exit_code": 0,
            },
            val16_rec,
            {
                "stage": "review_completion",
                "role": "completion",
                "iteration": 16,
                "output": str(rev16_out),
                "task_id": "task-validate-m3",
                "source_revision": snap["revision"],
                "exit_code": 0,
            },
            {
                "stage": "investigate_stuck",
                "role": "task_planner",
                "iteration": 17,
                "output": str(inv17_out),
                "source_revision": snap["revision"],
                "exit_code": 0,
            },
            rev17_rec,
        ]
        state["builder_retry_key"] = m_key
        state["builder_retries"] = {
            m_key: {
                "initial_route": {"engine": "opencode", "model": "zai-coding-plan/glm-5.3", "reasoning_effort": "high"},
                "checker_routes": {
                    "tester": {"engine": "opencode", "model": "openai/gpt-6-sol", "reasoning_effort": "xhigh"},
                    "completion": {"engine": "opencode", "model": "openai/gpt-6-sol", "reasoning_effort": "high"},
                },
                "failures": [
                    str(self.run / "iterations" / "013" / "validator-01.json"),
                    str(rev16_out),
                ],
                "ledger": [
                    {
                        "evidence": str(self.run / "iterations" / "013" / "validator-01.json"),
                        "failing": ["criterion:C1"],
                        "changed": ["gfs/server.py"],
                        "novelty": "new",
                        "signature": "sig-13",
                    },
                    {
                        "evidence": str(rev16_out),
                        "failing": ["criterion:C1", "finding:Race"],
                        "changed": ["gfs/server.py"],
                        "novelty": "new",
                        "signature": "sig-16",
                    },
                ],
                "position": 2,
                "action": "escalate",
            }
        }
        state["builder_retry_decisions"] = [
            {
                "action": "escalate",
                "milestone_key": m_key,
                "failure": str(rev16_out),
                "failure_class": "execution",
                "selected_model": "openai/gpt-6-sol",
                "selected_effort": "xhigh",
                "checker_models": {"tester": "zai-coding-plan/glm-5.3", "completion": "zai-coding-plan/glm-5.3"},
            }
        ]
        state["stuck_investigations"] = [
            {
                "identity": "builder-failure:13",
                "trigger": "builder_failure",
                "milestone_key": m_key,
                "outcome": "classified",
                "failure_class": "execution",
                "cause": "other",
            },
            {
                "identity": "builder-failure:14",
                "trigger": "builder_failure",
                "milestone_key": m_key,
                "outcome": "classified",
                "failure_class": "plan",
                "cause": "other",
            },
            {
                "identity": "builder-failure:16",
                "trigger": "builder_failure",
                "milestone_key": m_key,
                "outcome": "classified",
                "failure_class": "execution",
                "cause": "other",
                "failure_id": "prior-exec-id",
                "output": str(inv17_out),
            },
        ]
        autopilot.findings_ledger.record_decision(state, rev17_decision, rev17_rec)
        rev17_out.write_text(json.dumps(rev17_decision))
        open_id = state["findings_ledger"][0]["id"]
        autopilot.queue_resolution(state, rev17_decision, rev17_rec)

        resolver_value = {
            **rev17_decision,
            "diagnosis": "Hold mutation lock across read_file.",
            "evidence": [str(val16_out)],
            "recovery_change": {
                "hypothesis": "Acquiring lock prevents mixed-generation chunk reads.",
                "question": "Does holding lock prevent AAAADDDD under concurrent rewrite?",
                "target": "gfs/server.py",
                "before": "    def read_file(self, path: str) -> bytes:\n        return b'AAAADDDD'\n",
                "after": "    def read_file(self, path: str) -> bytes:\n        with self._lock:\n            return b'AAAABBBB'\n",
                "expected_check": probe_cmd,
                "expected_result": "Inverted defect probe no longer observes AAAADDDD.",
                "evidence_refs": ["event:cmd-1"],
            },
            "next_task": {
                "kind": "implement",
                "milestone_id": "M3",
                "acceptance_criteria": ["C1"],
                "findings": [open_id],
                "requirements": ["Hold lock in gfs/server.py read_file and add concurrent regression test."],
                "validation_plan": ["python3 -m unittest discover -s tests -t ."],
            },
        }
        res17_out = self.run / "iterations" / "017" / "resolver-01.json"
        res17_events = self.run / "iterations" / "017" / "resolver-01.jsonl"
        res17_out.write_text(json.dumps(resolver_value))
        res17_events.write_text('{"type": "done"}\n')
        res17_rec = {
            "stage": "resolve",
            "role": "task_planner",
            "iteration": 17,
            "output": str(res17_out),
            "events": str(res17_events),
            "source_revision": snap["revision"],
            "changed_files": [],
            "exit_code": 0,
        }

        # 1. Live prevention: applying 017/resolver-01.json reuses the same-round execution diagnosis,
        # attests recovery_change_id against the exit-0 Validator defect probe, does NOT double-charge
        # builder_retries, and assigns the M3 implement task without pausing PAUSED_BUILDER_CLASSIFICATION!
        live_state = copy.deepcopy(state)
        runner.apply_result(
            live_state, "resolve", copy.deepcopy(resolver_value), copy.deepcopy(res17_rec), self.root, self.run
        )
        self.assertEqual("RUNNING", live_state["status"])
        self.assertEqual("builder", live_state["next_stage"])
        self.assertNotIn("builder_failure_hold", live_state)
        self.assertNotIn("resolution_request", live_state)
        self.assertEqual("implement", live_state["current_task"]["kind"])
        self.assertEqual(2, live_state["builder_retries"][m_key]["position"])
        self.assertEqual("escalate", live_state["builder_retries"][m_key]["action"])
        self.assertIsNotNone(live_state["repair_plan"].get("recovery_change_id"))

        # 2. Saved-checkpoint reconciliation (exact gfs dccfee14 topology on --resume-paused):
        saved_state = copy.deepcopy(state)
        autopilot.unit_module("resolve").validate(saved_state, resolver_value, res17_rec, self.root)
        saved_state["stages"].append(copy.deepcopy(res17_rec))
        ev17 = autopilot.builder_failure.evidence(saved_state, rev17_rec)
        autopilot.builder_failure.hold(
            saved_state,
            ev17,
            "PAUSED_BUILDER_CLASSIFICATION",
            resolver_value["diagnosis"] + "; failure classification requires reconciled evidence or operator action",
        )
        run_records.write_json(self.run / "state.json", saved_state)
        with patch.object(runner.opencode, "check_models", return_value=None):
            args_r, parser = runner.cli_args.parse(
                None,
                ["--workspace", str(self.root), "--run-dir", str(self.run), "--resume-paused", "--no-chat"],
                runner.opencode.DEFAULT_MODELS,
            )
            self.assertIsNone(
                run_actions.handle(runner, args_r, parser, saved_state, self.run / "state.json", self.run, self.root)
            )
        self.assertEqual("RUNNING", saved_state["status"])
        self.assertEqual("builder", saved_state["next_stage"])
        self.assertNotIn("builder_failure_hold", saved_state)
        self.assertNotIn("resolution_request", saved_state)
        self.assertEqual("implement", saved_state["current_task"]["kind"])
        self.assertEqual("openai/gpt-6-sol", saved_state["settings"]["roles"]["builder"]["model"])
        self.assertIsNotNone(saved_state["repair_plan"].get("recovery_change_id"))
        autopilot.builder_failure.dispatch_guard(saved_state, "builder", self.root)
        builder_policy.guard(saved_state)
        runner.milestones.dispatch_guard(saved_state, "builder")
        build_rec = {
            "stage": "builder",
            "output": str(self.run / "iterations" / "018" / "builder-01.json"),
            "started_at": "2026-11-16T21:30:00+00:00",
        }
        rr.admit_dispatch(saved_state, build_rec, self.root, self.run)
        self.assertEqual("repair", build_rec["recovery_novelty"]["action"])

    def test_stuck_job_retire_reads_pause_code_categories_without_hardcoded_list(self):
        import autocode_pause_codes as pause_codes

        state = {
            "task": "Build GFS",
            "source_snapshot": {"revision": "rev-1"},
            "goal_contract": {"revision": 1, "hash": "h-1"},
            "answers": {},
            "brief_feedback": [],
        }
        binding = stuck_job.recovery_binding.binding(state, "tester")
        for status, category in pause_codes.CODES.items():
            if category in ("budget", "environment"):
                s = copy.deepcopy(state)
                s["stuck_investigation"] = {
                    "identity": "tester:PAUSED_REPEATED_FAILURE",
                    "stage": "tester",
                    "in_force": True,
                    **binding,
                }
                stuck_job.retire(s, status)
                self.assertIn("stuck_investigation", s, f"{status} ({category}) must preserve in-force guidance")


if __name__ == "__main__":
    unittest.main()
