"""Regression tests for structural planning runner-event predicates and view/pause_authority feedback agreement."""

from __future__ import annotations

import copy
import unittest
from pathlib import Path

import autocode as runner
import autocode_draft_examples as draft_examples
import autocode_draft_failure_correction as failure_correction
import autocode_goals as goals
import autocode_limit_events as limit_events
import autocode_pause_authority as pause_authority
import autocode_plan_retention as plan_retention
import autocode_planning_feedback as planning_feedback
import autocode_recovery_view as recovery_view
import autocode_run_view as run_view
import autocode_util as util
import autopilot
import goal_fixtures

from . import test_draft_failure_correction as dfc_fixture


class SharedCauseARunnerEventsTests(unittest.TestCase):
    """Runner machinery events in state['user_events'] never invalidate planning receipts or masks."""

    def test_readonly_planning_runner_events_preserve_failure_correction_snapshot_and_revise_receipts(self):
        state, review, changed, changes = dfc_fixture.fixture(compatibility=True)
        initial_snapshot = failure_correction.snapshot(state)
        autopilot.apply_planning(state, "review_plan", review, {"output": "review.json"})
        self.assertEqual("revise_plan", state["next_stage"])

        runner_events = [
            {
                "kind": "automatic_unlaunchable_repair_restart",
                "actor": "runner",
                "at": "2026-11-16T09:10:00+00:00",
                "stage": "revise_plan",
                "source_revision": "rev-1",
                "reason": "Complete report-repair handoff exceeds 262144 bytes.",
            },
            {
                "kind": "automatic_timeout_recovery",
                "actor": "runner",
                "at": "2026-11-16T09:20:00+00:00",
                "attempt_id": "001/requirements-revise-03",
                "next_stage": "revise_plan",
                "changed_files": [],
            },
            {
                "kind": "automatic_provider_network_recovery",
                "actor": "runner",
                "at": "2026-11-16T09:25:00+00:00",
                "role": "planner",
            },
            {
                "kind": "route_assignment",
                "actor": "runner",
                "at": "2026-11-16T09:26:00+00:00",
                "role": "planner",
            },
        ]
        state["user_events"].extend(runner_events)
        self.assertEqual(initial_snapshot, failure_correction.snapshot(state))
        self.assertEqual(1, len(failure_correction.receipts(state, review)))
        runner.lifecycle.install_draft(state, changed, origin="revise_plan", changes=changes)
        self.assertEqual(dfc_fixture.AFTER, state["goal_contract"]["body"]["required_behaviors"][-1])

    def test_person_feedback_or_execution_recovery_still_invalidates_failure_correction_snapshot(self):
        for event in (
            {"kind": "brief_feedback", "actor": "user_cli", "text": "Keep original behavior"},
            {"kind": "automatic_timeout_recovery", "actor": "runner", "next_stage": "builder", "changed_files": []},
            {
                "kind": "automatic_timeout_recovery",
                "actor": "runner",
                "next_stage": "revise_plan",
                "changed_files": ["app.py"],
            },
        ):
            with self.subTest(event=event):
                state, review, _, _ = dfc_fixture.fixture(compatibility=True)
                before = failure_correction.snapshot(state)
                state["user_events"].append(event)
                self.assertNotEqual(before, failure_correction.snapshot(state))
                with self.assertRaises(ValueError):
                    failure_correction.receipts(state, review)

    def test_neighbour_readers_ignore_runner_machinery_events(self):
        state = {
            "task": "Build search",
            "answers": {},
            "brief_feedback": [],
            "design_constraint": None,
            "user_events": [
                {"kind": "goal_approval", "actor": "user_cli", "token": "r1:abc"},
                {
                    "kind": "automatic_unlaunchable_repair_restart",
                    "actor": "runner",
                    "stage": "revise_plan",
                    "reason": "secret-runner-only-literal-42",
                },
            ],
            "planning": {
                "reports": {
                    "review_plan": {
                        "report": {
                            "concerns": [
                                {
                                    "id": "C1",
                                    "concern": "Need literal example",
                                    "requested_change": "Specify output",
                                    "acceptance_test": "assert 'expected-review-example' in out",
                                    "blocking": True,
                                }
                            ]
                        }
                    }
                }
            },
        }
        # 1. limit_events.last_decision keeps the person's goal_approval instead of runner bookkeeping.
        self.assertEqual(
            {"kind": "goal_approval", "actor": "user_cli", "token": "r1:abc"}, limit_events.last_decision(state)
        )
        # 2. draft_examples.corrections never treats runner error strings as user-typed literals blocking correction.
        before_json = '{"count": 41}'
        after_json = '{"count": 42}'
        example_state = {
            "task": "Build counter",
            "answers": {},
            "brief_feedback": [],
            "design_constraint": None,
            "goal_contract": {"approval_status": "draft", "approval_event": None, "origin": "revise_plan"},
            "planning": {
                "reports": {
                    "review_plan": {
                        "report": {
                            "concerns": [
                                {
                                    "id": "C1",
                                    "blocking": True,
                                    "concern": f"AC1 outputs {before_json} instead of {after_json}",
                                }
                            ]
                        }
                    }
                }
            },
            "user_events": [
                {
                    "kind": "automatic_unlaunchable_repair_restart",
                    "actor": "runner",
                    "stage": "revise_plan",
                    "reason": f"runner log {before_json}",
                }
            ],
        }
        before_body = {
            "acceptance_criteria": [{"id": "AC1", "criterion": f"The command prints `{before_json}` to stdout."}]
        }
        after_body = {
            "acceptance_criteria": [{"id": "AC1", "criterion": f"The command prints `{after_json}` to stdout."}]
        }
        changes = [
            {
                "item": "AC1",
                "change": "reworded",
                "basis": "agent_proposed",
                "answer_id": "",
                "replacement": f"The command prints `{after_json}` to stdout.",
                "example_correction": {"concern_id": "C1", "before": before_json, "after": after_json},
            }
        ]
        no_user_basis = lambda *_args: False  # noqa: E731
        self.assertEqual(
            {"AC1"}, draft_examples.corrections(example_state, before_body, after_body, changes, no_user_basis)
        )
        example_state["user_events"].append(
            {"kind": "brief_feedback", "actor": "user_cli", "text": f"Use {before_json}"}
        )
        self.assertEqual(
            set(), draft_examples.corrections(example_state, before_body, after_body, changes, no_user_basis)
        )
        # 3. plan_retention.prior_review_examples survives read-only planning runner events on a draft.
        ac8 = {
            "id": "AC8",
            "criterion": "Given todos.json containing ID 1, bad usage leaves the bytes unchanged.",
            "verification_method": "guard: test_bad_usage",
            "human_review": False,
            "sources": [{"source_id": "task:0", "quote": "Preserve bytes."}],
        }
        ac9 = {**ac8, "id": "AC9", "current_behavior": "Old count: 3"}
        prior_body = {
            "task_kind": "bugfix",
            "acceptance_criteria": [ac8, ac9],
            "permission_boundaries": ["No network"],
            "required_behaviors": ["Preserve bytes."],
            "scope_exclusions": [],
            "constraints": [],
            "important_failure_cases": [],
            "milestones": [
                {
                    "id": "M1",
                    "objective": "Fix the bug",
                    "acceptance_criteria": ["AC8", "AC9"],
                    "affected_paths": ["todos.py"],
                    "depends_on": [],
                }
            ],
            "initial_task": {
                "milestone_id": "M1",
                "objective": "Fix the bug",
                "acceptance_criteria": ["AC8", "AC9"],
                "affected_paths": ["todos.py"],
                "validation_plan": ["python3 -m unittest"],
            },
            "technical_approach": ["Fix the bug and test it"],
        }
        prior_draft = {
            "task_id": "small-fix",
            "revision": 1,
            "body": prior_body,
            "approval_status": "draft",
            "origin": "planner_draft",
        }
        prior_draft["hash"] = util.digest({k: prior_draft[k] for k in ("task_id", "revision", "body")})
        revised_draft = copy.deepcopy(prior_draft)
        revised_draft.update(revision=2, origin="revise_plan")
        revised_draft["body"]["acceptance_criteria"][0]["verification_method"] = "guard: test_ac8_bad_usage"
        revised_draft["body"]["acceptance_criteria"][1]["current_behavior"] = "Old count: 4"
        revised_draft["hash"] = util.digest({k: revised_draft[k] for k in ("task_id", "revision", "body")})
        draft_state = {
            "task": "Build search",
            "iteration": 1,
            "answers": {},
            "brief_feedback": [],
            "goal_contract": revised_draft,
            "contract_history": [prior_draft],
            "planning": {
                "reports": {
                    "review_plan": {
                        "output": "accepted-review.json",
                        "report": {"summary": "AC8: run with ID 1, output bytes unchanged."},
                    }
                }
            },
            "stages": [
                {
                    "stage": "review_plan",
                    "iteration": 1,
                    "contract_hash": prior_draft["hash"],
                    "source_revision": "source-1",
                    "route_role": "plan_reviewer",
                    "changed_files": [],
                    "exit_code": 0,
                    "finished_at": "2026-11-06T00:00:00Z",
                    "output": "accepted-review.json",
                }
            ],
            "user_events": [
                {
                    "kind": "automatic_unlaunchable_repair_restart",
                    "actor": "runner",
                    "stage": "revise_plan",
                    "reason": "overflow",
                }
            ],
        }
        reused = plan_retention.prior_review_examples(draft_state, "plan_reviewer", lambda: "source-1")
        self.assertIsNotNone(reused)
        self.assertEqual(["AC8"], reused["unchanged_example_ids"])
        draft_state["user_events"].append({"kind": "brief_feedback", "actor": "user_cli", "text": "Use 201"})
        self.assertIsNone(plan_retention.prior_review_examples(draft_state, "plan_reviewer", lambda: "source-1"))


class SharedCauseCViewAndPauseAuthorityAgreementTests(unittest.TestCase):
    """View recovery actions, chat composer, and pause_authority agree from one shared rule."""

    def _base_state(self, status: str, next_stage: str, *, approved: bool) -> dict:
        state = {
            "version": 3,
            "workspace": "/ws",
            "run_dir": "/ws/.autocode/runs/r1",
            "task": "Build search",
            "task_id": "task-1",
            "status": status,
            "phase": "PAUSED_OR_BLOCKED",
            "next_stage": next_stage,
            "iteration": 1,
            "settings": {"roles": {"builder": {"model": "m1"}, "tester": {"model": "m2"}}, "limits": {}},
            "stages": [],
            "history": [],
            "user_events": [],
        }
        goal_fixtures.approve_fixture(state, goals)
        if not approved:
            state["goal_contract"].update(approval_status="draft", approval_event=None)
            state["user_events"] = []
        state.update(status=status, phase="PAUSED_OR_BLOCKED", next_stage=next_stage)
        return state

    def test_unapproved_planning_non_convergence_pauses_offer_and_admit_feedback(self):
        for status in ("PAUSED_REPEATED_FAILURE", "PAUSED_REPORT_REPAIR_LIMIT"):
            for stage in ("requirements_gather", "draft_plan", "review_plan", "revise_plan", "finalize_plan"):
                with self.subTest(status=status, stage=stage):
                    state = self._base_state(status, stage, approved=False)
                    self.assertTrue(planning_feedback.planning_report_stop(state))
                    self.assertIsNone(pause_authority.feedback_refusal(state))
                    view = run_view.view(state, workspace=Path("/ws"), run_dir=Path("/ws/.autocode/runs/r1"))
                    self.assertIn("feedback", [a["kind"] for a in view["recovery"]["actions"]])
                    self.assertEqual("feedback", (view["chat"]["input"] or {}).get("kind"))
                    goals.feedback(state, "Keep existing escaping contract unchanged.")
                    self.assertEqual("RUNNING", state["status"])

    def test_approved_execution_operational_pauses_neither_offer_nor_admit_feedback(self):
        for status in (
            "PAUSED_REPEATED_FAILURE",
            "PAUSED_REPORT_REPAIR_LIMIT",
            "PAUSED_COST_LIMIT",
            "PAUSED_TIME_LIMIT",
        ):
            with self.subTest(status=status):
                state = self._base_state(status, "builder", approved=True)
                self.assertFalse(planning_feedback.planning_report_stop(state))
                self.assertIsNotNone(pause_authority.feedback_refusal(state))
                card = recovery_view.project(state, {"kind": "resume"})
                self.assertNotIn("feedback", [a["kind"] for a in card["actions"]])
                view = run_view.view(state, workspace=Path("/ws"), run_dir=Path("/ws/.autocode/runs/r1"))
                self.assertIsNone(view["chat"]["input"])

    def test_repeated_failure_with_dispatchable_repair_labels_resume_honestly_not_edited_source(self):
        state = self._base_state("PAUSED_REPEATED_FAILURE", "tester", approved=True)
        state["pending_report_repair"] = {"attempts": 1, "original": {"stage": "tester"}}
        state["settings"]["report_repair"] = {"max_attempts": 2}
        view = run_view.view(state, workspace=Path("/ws"), run_dir=Path("/ws/.autocode/runs/r1"))
        resume_action = next(a for a in view["recovery"]["actions"] if a["kind"] == "resume")
        self.assertEqual("Resume report repair", resume_action["label"])
        self.assertNotEqual("Validate the edited source", resume_action["label"])


class SharedCauseKStructuralPlanningRunnerEventsTests(unittest.TestCase):
    """Task 1: structural planning runner event predicate replaces hardcoded RUNNER_PLANNING_EVENTS."""

    def test_unlisted_future_planning_runner_events_and_runner_abandonments_preserve_snapshot(self):
        self.assertFalse(hasattr(failure_correction, "RUNNER_PLANNING_EVENTS"))
        state, review, changed, changes = dfc_fixture.fixture(compatibility=True)
        initial_snapshot = failure_correction.snapshot(state)
        autopilot.apply_planning(state, "review_plan", review, {"output": "review.json"})

        state["user_events"].extend(
            [
                {
                    "kind": "automatic_future_planning_bookkeeping",
                    "actor": "runner",
                    "at": "2026-11-16T20:00:00+00:00",
                    "stage": "revise_plan",
                    "changed_files": [],
                },
                {
                    "kind": "stage_abandoned",
                    "actor": "runner",
                    "at": "2026-11-16T20:01:00+00:00",
                    "attempt_id": "001/plan-challenge-01",
                    "changed_files": [],
                },
                {
                    "kind": "stage_abandoned",
                    "actor": "runner",
                    "at": "2026-11-16T20:02:00+00:00",
                    "attempt_id": "001/requirements-revise-report-repair-01",
                    "changed_files": [],
                },
                {
                    "kind": "future_role_route_note",
                    "actor": "runner",
                    "at": "2026-11-16T20:03:00+00:00",
                    "role": "plan_reviewer",
                },
            ]
        )
        self.assertEqual([], failure_correction.source_events(state))
        self.assertEqual(initial_snapshot, failure_correction.snapshot(state))
        self.assertEqual(1, len(failure_correction.receipts(state, review)))
        runner.lifecycle.install_draft(state, changed, origin="revise_plan", changes=changes)
        self.assertEqual(dfc_fixture.AFTER, state["goal_contract"]["body"]["required_behaviors"][-1])

    def test_structural_mutations_human_gates_and_execution_targets_still_invalidate_snapshot(self):
        disqualifiers = (
            {
                "kind": "stage_abandoned",
                "actor": "user_cli",
                "attempt_id": "001/plan-challenge-01",
                "changed_files": [],
            },
            {"kind": "stage_abandoned", "actor": "runner", "attempt_id": "001/builder-01", "changed_files": []},
            {"kind": "stage_abandoned", "actor": "runner", "attempt_id": "001/validator-01", "changed_files": []},
            {
                "kind": "stale_handoff_paused",
                "actor": "runner",
                "attempt_id": "001/plan-challenge-01",
                "compared_binding": {"source": "a"},
                "changed": ["source"],
            },
            {
                "kind": "stale_handoff_discarded",
                "actor": "runner",
                "attempt_id": "001/plan-challenge-02",
                "paused_attempt_id": "001/plan-challenge-01",
            },
            {"kind": "review_request_rerouted", "actor": "runner", "milestone_id": "M1"},
            {"kind": "review_reconciliation", "actor": "runner", "criterion": "C1", "answer_id": "Q1"},
            {"kind": "runner_check_retired", "actor": "runner", "reason": "processes gone"},
            {"kind": "brief_feedback", "actor": "runner", "stage": "revise_plan"},
        )
        for event in disqualifiers:
            with self.subTest(event=event):
                state, review, _, _ = dfc_fixture.fixture(compatibility=True)
                before = failure_correction.snapshot(state)
                state["user_events"].append(event)
                self.assertEqual([event], failure_correction.source_events(state))
                self.assertNotEqual(before, failure_correction.snapshot(state))
                with self.assertRaises(ValueError):
                    failure_correction.receipts(state, review)


if __name__ == "__main__":
    unittest.main()
