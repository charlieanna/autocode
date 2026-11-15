"""Internal exhaustion cannot create or accept a machinery question; no provider is launched."""

import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import autocode_builder_policy as builder_policy
import autocode_builder_recovery as builder_recovery
import autocode_failures as failures
import autocode_goal_lifecycle as lifecycle
import autocode_goals as goals
import autocode_interventions as interventions
import autocode_limit_events as limit_events
import autocode_metadata_custody as custody
import autocode_milestones as milestones
import autocode_operational_admission as admission
import autocode_operational_block as typed_block
import autocode_operational_disposition as disposition
import autocode_operational_information as information
import autocode_operational_receipt_identity as receipt_identity
import autocode_recovery_grants as recovery_grants
import autocode_resolver_human as human
import autocode_resolver_runtime as runtime
import autocode_run_actions as actions
import autocode_run_view as run_view
import autocode_source_log as source_log
import autocode_stage_recovery as stage_recovery
import autocode_stop as stop
import autocode_support as support
import autocode_util as util
from goal_fixtures import body


class Arguments(SimpleNamespace):
    def __getattr__(self, name):
        return None


class OperationalDispositionTests(unittest.TestCase):
    def setUp(self):
        self.spawn = self.enterContext(patch.object(subprocess, "Popen", side_effect=AssertionError("no process")))
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.state = {
            "task": "Review the requested change",
            "workspace": str(self.root),
            "run_dir": str(self.root / "run"),
            "iteration": 1,
            "status": "PAUSED_PROVIDER_UNCERTAIN",
            "next_stage": "requirements_gather",
            "settings": {"limits": {"max_cost_usd": 0, "planning_calls": 1}},
            "stages": [],
            "active_stage": {
                "stage": "requirements_gather",
                "role": "requirements",
                "iteration": 1,
                "events": str(self.root / "requirements.jsonl"),
                "output": str(self.root / "requirements.json"),
                "started_at": "2026-11-15T16:04:35+00:00",
                "finished_at": "2026-11-15T16:04:36+00:00",
                "exit_code": 1,
                "accounted": True,
            },
        }
        self.run = Path(self.state["run_dir"])
        self.enterContext(patch.object(human, "_binding", side_effect=self.binding))
        self.enterContext(
            patch.object(util, "runner_identity", return_value={"path": str(self.root), "commit": "fixture"})
        )
        self.enterContext(
            patch.object(human, "pending_interruptions", return_value={"pending": [], "pause_requested": False})
        )
        self.runner = SimpleNamespace(recovery_count=lambda state: 0, MAX_AUTOMATIC_RECOVERIES=3)

    def tearDown(self):
        self.spawn.assert_not_called()

    def binding(self, state):
        return {
            "task": state["task"],
            "workspace": state["workspace"],
            "run_dir": state["run_dir"],
            "task_id": state.get("task_id"),
            "current_task": util.digest(state.get("current_task")),
            "contract": human._contract(state),
            "user_events": util.digest(state.get("user_events", [])),
            "failure_history": util.digest(state.get("failure_history", {})),
            "settings": util.digest(state["settings"]),
            "next_stage": state.get("next_stage"),
            "accounting": {
                key: copy.deepcopy(state.get(key))
                for key in (
                    "automatic_recoveries_since_resume",
                    "automatic_timeout_recoveries",
                    "consecutive_timeout_recoveries",
                )
            },
            "active_stage": util.digest(state.get("active_stage")),
            "pending_report_repair": util.digest(state.get("pending_report_repair")),
            "interruptions": {"pending": [], "pause_requested": False},
            "runtime_sources": "fixture-runtime",
            "private_control": "PRIVATE-FAMILY-ROOT-GROUP-MEMBER",
        }

    def legacy(self, *, scope="operational_exhaustion", pause="PAUSED_PROVIDER_UNCERTAIN", budget=None, questions=None):
        """An exact old runner publication, independent of today's publication policy."""
        self.state["status"] = pause
        budget = budget or {"category": "operational_recovery", "kind": None, "origin": "unknown", "limit": None}
        stage = self.state["next_stage"]
        receipt = runtime._operational_receipt(
            self.state,
            self.run,
            "hold",
            "Retained provider failure",
            {
                "status": pause,
                "stage": stage,
                "budget": budget,
                "plan_review_calls": (self.state.get("planning") or {}).get("plan_review_calls"),
                "recovery_calls_used": (self.state.get("planning") or {}).get("recovery_review_calls_used", 0),
            },
        )
        human.queue(
            self.state,
            scope,
            {"stage": stage, "pause_status": pause, "budget": budget},
            request={
                "kind": "blocker",
                "discovered": "Provider exited without a report",
                "impact": "The retained attempt is uncertain",
                "decision_needed": "Provide corrective information",
                "options": ["Provide corrective information", "Leave paused"],
            },
            evidence={"resolver_receipt_id": receipt},
            questions=questions,
            next_stage=stage,
        )
        proposal = copy.deepcopy(self.state[human.PRIVATE])
        identity = {
            "version": 1,
            "issuer": "resolver",
            "binding": self.binding(self.state),
            "proposal": proposal,
            "action": "escalate",
            "reason": "Old runner operational hold",
        }
        self.state["resolver"].setdefault("human_escalations", {})
        return human._publish(self.state, identity)

    def consumed(self):
        public = self.legacy()
        event = {
            "actor": "user_cli",
            "request_id": public["request_id"],
            "request_token": public["request_token"],
            "action": "provide_information",
            "text": "The environment was repaired",
            "at": "2026-11-15T16:05:00+00:00",
        }
        entry = self.state["resolver"]["human_escalations"][public["request_id"]]
        entry.update(status="consumed", response=copy.deepcopy(event))
        self.state["resolver"]["human_responses"] = [copy.deepcopy(event)]
        self.state["status"] = "PAUSED_PROVIDER_UNCERTAIN"
        for key in (human.PUBLIC, "user_request"):
            self.state.pop(key, None)
        self.state["pending_questions"] = []
        return public, event, entry

    def test_fresh_producer_reports_internal_failure_without_a_question(self):
        self.assertTrue(
            runtime.record_operational_exhaustion(
                self.runner, self.state, self.run, support.Paused("PAUSED_PROVIDER_UNCERTAIN", "requirements exited1")
            )
        )
        if self.state.get(human.PRIVATE):
            human.evaluate(self.state)
        self.assertIsNone(human.current(self.state))
        self.assertEqual([], self.state.get("pending_questions", []))
        self.assertEqual(1, self.state["active_stage"]["exit_code"])
        self.assertEqual(1, self.state["settings"]["limits"]["planning_calls"])

    def test_authentic_legacy_reply_is_refused_before_any_response_is_saved(self):
        public = self.legacy()
        before = copy.deepcopy(self.state)
        with self.assertRaises(ValueError):
            human.respond_operational(
                self.state,
                public["request_id"],
                public["request_token"],
                "provide_information",
                "The environment was repaired",
            )
        self.assertEqual(before, self.state)

    def test_mutable_queued_hints_or_removal_never_erase_the_original_recorded_incident(self):
        original = self.queued()
        pristine = copy.deepcopy(self.state)
        diagnostic = copy.deepcopy(self.state["resolver"]["operational_receipts"][original])
        route = {
            "id": "route-builder",
            "route_role": "builder",
            "category": "quota",
            "question": "Which saved model?",
            "options": [],
            "proposed_default": "",
        }
        for change in (True, "false", "model_question", "missing_questions", "removed_proposal"):
            with self.subTest(change=change):
                self.state = copy.deepcopy(pristine)
                proposal = self.state[human.PRIVATE]
                if change in (True, "false"):
                    proposal["evidence"]["recovery_available"] = change
                elif change == "model_question":
                    proposal["questions"] = [route]
                elif change == "missing_questions":
                    proposal.pop("questions", None)
                else:
                    self.state.pop(human.PRIVATE)
                self.assertEqual(original, disposition.incident_identity(self.state, disposition.held(self.state)))
                self.assertEqual(
                    2,
                    admission.guard(
                        self.writer(),
                        Arguments(resume_paused=True),
                        None,
                        self.state,
                        self.run / "state.json",
                        self.run,
                        self.root,
                        actions.Decided(),
                    ),
                )
                self.assertEqual(diagnostic, self.state["resolver"]["operational_receipts"][original])
                self.assert_blocked_status()

    def test_mutated_legacy_publication_and_consumed_reply_keep_original_diagnostic_denial(self):
        public, event, entry = self.consumed()
        original = entry["identity"]["proposal"]["evidence"]["resolver_receipt_id"]
        diagnostic = copy.deepcopy(self.state["resolver"]["operational_receipts"][original])
        entry["identity"]["proposal"]["evidence"]["recovery_available"] = "false"
        before = copy.deepcopy(self.state)
        self.assertEqual(original, disposition.incident_identity(self.state, disposition.held(self.state)))
        with self.assertRaises(ValueError):
            human.respond_operational(
                self.state, public["request_id"], public["request_token"], event["action"], event["text"]
            )
        self.assertEqual(before, self.state)
        self.assertEqual(diagnostic, self.state["resolver"]["operational_receipts"][original])

    def test_fresh_producer_records_one_disposition_and_protected_model_question(self):
        pristine = copy.deepcopy(self.state)
        route = {
            "id": "route-builder",
            "route_role": "builder",
            "category": "quota",
            "question": "Which saved model?",
            "options": [],
            "proposed_default": "",
            "job": "Builder",
        }
        for status, expected in (
            ("PAUSED_PROVIDER_UNCERTAIN", "machinery"),
            ("PAUSED_COST_LIMIT", "limit"),
            ("PAUSED_BUDGET", "model"),
        ):
            with self.subTest(status=status):
                self.state = copy.deepcopy(pristine)
                self.state.update(next_stage="builder")
                self.state["active_stage"].update(stage="builder", role="builder")
                stopped = {"active": True, "attempt_id": "001/builder"} if expected == "model" else None
                with (
                    patch.object(runtime.quota_route, "stopped_attempt", return_value=stopped),
                    patch.object(runtime.quota_route, "question", return_value=route if stopped else None),
                ):
                    self.assertTrue(
                        runtime.record_operational_exhaustion(
                            self.runner, self.state, self.run, support.Paused(status, "Retained failure")
                        )
                    )
                identity = self.state["resolver"]["operational_diagnostics"][-1]
                row = self.state["resolver"]["operational_receipts"][identity]
                self.assertEqual(expected, row["receipt"]["evidence"]["disposition"])
                self.assertEqual(expected, disposition.record_disposition(self.state, row))
                if expected == "machinery":
                    self.assertIsNone(human.current(self.state))
                    self.assertIsNotNone(disposition.held(self.state))
                else:
                    proposal = self.state[human.PRIVATE]
                    self.assertEqual(expected, disposition.saved_disposition(self.state, proposal))
                    self.assertEqual("escalate", human.evaluate(self.state))
                    self.assertIsNotNone(human.current(self.state))
                    if expected == "model":
                        changed = copy.deepcopy(proposal)
                        changed["questions"] = []
                        changed["evidence"]["recovery_available"] = True
                        self.assertEqual("invalid", disposition.saved_disposition(self.state, changed))

    def test_consumed_legacy_reply_cannot_pass_the_idempotent_fast_path(self):
        public, event, _ = self.consumed()
        before = copy.deepcopy(self.state)
        with self.assertRaises(ValueError):
            human.respond_operational(
                self.state, public["request_id"], public["request_token"], event["action"], event["text"]
            )
        self.assertEqual(before, self.state)

    def test_recorded_question_bytes_bind_new_cap_choices_and_preserve_authentic_old_cap_questions(self):
        pristine = copy.deepcopy(self.state)
        unrecorded = [{"id": "unrecorded-choice", "question": "Which work?", "options": ["A", "B"]}]
        for status, origin in (
            ("PAUSED_COST_LIMIT", "user_explicit"),
            ("PAUSED_TIME_LIMIT", "user_explicit"),
            ("PAUSED_TIME_LIMIT", "unknown"),
        ):
            with self.subTest(status=status, origin=origin):
                self.state = copy.deepcopy(pristine)
                self.state["settings"].setdefault("budget_origins", {})["max_seconds"] = origin
                self.state["settings"]["limits"]["max_seconds"] = 60
                runtime.record_operational_exhaustion(
                    self.runner, self.state, self.run, support.Paused(status, "Saved cap")
                )
                original = self.state["resolver"]["operational_diagnostics"][-1]
                record = copy.deepcopy(self.state["resolver"]["operational_receipts"][original])
                self.assertEqual("limit", disposition.saved_disposition(self.state, self.state[human.PRIVATE]))
                self.state[human.PRIVATE]["questions"] = copy.deepcopy(unrecorded)
                self.assertEqual("invalid", disposition.saved_disposition(self.state, self.state[human.PRIVATE]))
                self.assertEqual("hold", human.evaluate(self.state))
                self.assertIsNone(human.current(self.state))
                self.assertEqual(record, self.state["resolver"]["operational_receipts"][original])

        self.state = copy.deepcopy(pristine)
        public = self.legacy(
            pause="PAUSED_TIME_LIMIT",
            budget={"kind": "max_seconds", "origin": "user_explicit", "limit": 60},
            questions=unrecorded,
        )
        self.assertEqual(public, human.current(self.state))
        proposal = self.state["resolver"]["human_escalations"][public["request_id"]]["identity"]["proposal"]
        self.assertEqual("limit", disposition.saved_disposition(self.state, proposal))
        self.assertEqual(unrecorded, public["questions"])
        self.assertIsNone(disposition.held(self.state))

    def test_stale_legacy_machinery_request_is_not_rebound_into_new_authority(self):
        public = self.legacy()
        self.state["settings"]["limits"]["unrelated"] = 7
        before = copy.deepcopy(self.state)
        self.assertIsNone(human.rebind_stale(self.state, public["request_id"], public["request_token"]))
        self.assertEqual(before, self.state)

    def test_legacy_information_cannot_schedule_an_internal_recovery_review(self):
        _, event, entry = self.consumed()
        frontier = {
            "binding": self.binding(self.state),
            "pause_status": self.state["status"],
            "request_id": event["request_id"],
        }
        before = copy.deepcopy(self.state)
        self.assertIsNone(information.schedule(self.state, event, entry, frontier))
        self.assertEqual(before, self.state)

    def test_public_legacy_projection_has_no_question_resume_or_private_control(self):
        self.legacy()
        before = copy.deepcopy(self.state)
        shown = run_view.view(self.state, run_dir=self.run, workspace=self.root)
        self.assertIsNone(shown["needs"])
        self.assertIsNone(shown["next_command"])
        self.assertIsNone(shown["chat"]["input"])
        self.assertEqual("blocked", shown["recovery"]["category"])
        self.assertFalse(shown["done"])
        self.assertNotIn("PRIVATE-FAMILY-ROOT-GROUP-MEMBER", str(shown))
        self.assertEqual(before, self.state)

    def test_refused_public_request_does_not_sample_a_new_source_binding(self):
        self.legacy()
        with patch.object(human, "_binding", side_effect=AssertionError("known refusal needs no fresh source")):
            self.assertIsNone(human.current(self.state))
            shown = human.projection(self.state)
            self.assertFalse(shown["human_request_authorized"])
            self.assertIsNone(shown["human_escalation"])
            self.assertEqual([], shown["pending_questions"])

    def test_historical_fixture_constructor_never_reauthorizes_its_cached_reply(self):
        from .operational_fixtures import legacy_publication, legacy_response

        public = legacy_publication(self.state, self.run)
        self.assertEqual(public, human.retained(self.state))
        self.assertIsNone(human.current(self.state))
        event = legacy_response(self.state, public)
        before = copy.deepcopy(self.state)
        with self.assertRaises(ValueError):
            human.respond_operational(
                self.state, public["request_id"], public["request_token"], event["action"], event["text"]
            )
        self.assertEqual(before, self.state)
        self.assertIsNotNone(disposition.held(self.state))

    def test_startup_failure_uses_original_diagnostic_without_requiring_a_person_question(self):
        import autocode_startup_timeout_view as startup_view

        self.state.update(
            automatic_recoveries_since_resume=3,
            consecutive_timeout_recoveries=3,
            automatic_timeout_recoveries=[
                {
                    "stage": self.state["next_stage"],
                    "timeout_kind": "idle",
                    "stall_phase": "before_first_event",
                    "completed_tool_calls": 0,
                }
                for _ in range(3)
            ],
        )
        runtime.record_operational_exhaustion(
            self.runner, self.state, self.run, support.Paused("PAUSED_TIMEOUT_RECOVERY", "Startup exhausted")
        )
        self.assertIsNone(human.current(self.state))
        self.assertNotIn(human.PUBLIC, self.state)
        self.assertEqual("AutoCode could not start the model step", startup_view.presentation(self.state)["title"])
        self.assert_blocked_status()
        self.state["automatic_timeout_recoveries"][-1]["stall_phase"] = "during_reasoning_or_output"
        self.assertNotEqual("AutoCode could not start the model step", startup_view.presentation(self.state)["title"])
        self.assert_blocked_status()

    def queued(self, pause="PAUSED_PROVIDER_UNCERTAIN"):
        public = self.legacy(pause=pause)
        entry = self.state["resolver"]["human_escalations"].pop(public["request_id"])
        self.state[human.PRIVATE] = copy.deepcopy(entry["identity"]["proposal"])
        self.state.pop(human.PUBLIC, None)
        self.state.pop("user_request", None)
        self.state.update(status="RESOLVER_PENDING", pending_questions=[])
        return disposition.held(self.state)["receipt"]["id"]

    def writer(self):
        return SimpleNamespace(
            now=support.now,
            resolver_runtime=runtime,
            resolver_human=human,
            milestones=milestones,
            write_json=support.atomic_json,
        )

    def recovery_writer(self):
        import autocode as core

        writer = self.writer()
        for name in (
            "prepare_abandoned_completion_revalidation",
            "prepare_timeout_archived_completion",
            "prepare_planning_retry",
            "prepare_exhausted_execution_report_retry",
            "archive_stale_report_repair",
            "repeated_failure_resume_guard",
            "reconcile_active",
            "reset_report_repair_for_resume",
            "ReportRepairQueued",
            "authorize_failure_retry",
            "resolver_recovery",
            "autopilot",
            "interventions",
            "planning",
            "timeout_recovery_guard",
            "abandon_stage",
        ):
            setattr(writer, name, getattr(core, name))
        return writer

    def test_plain_resume_is_refused_before_epochs_or_report_repairs_can_change(self):
        self.legacy()
        self.state.update(automatic_recoveries_since_resume=3, pending_report_repair={"attempts": 2})
        self.state["resolver"]["epoch"] = 7
        before = copy.deepcopy(self.state)
        args = Arguments(resume_paused=True, _explicit_budget_flags=set())
        visited = []

        def forbidden(*_):
            visited.append("mutating recovery")
            self.state["resolver"]["epoch"] = 0

        # The actual public prefix must refuse before its first advancing step.
        prefix = actions.STEPS[: actions.STEPS.index(admission.guard) + 1]
        with patch.object(actions, "STEPS", (*prefix, forbidden)):
            self.assertEqual(
                2, actions.handle(self.writer(), args, None, self.state, self.run / "state.json", self.run, self.root)
            )
        self.assertEqual([], visited)
        self.assertEqual(before, self.state)

    def planning_incident(self, *, queued=True):
        self.state.pop("active_stage")
        self.state.update(
            task_id="planning-task",
            next_stage="review_plan",
            user_events=[{"kind": "task", "id": "initial", "text": self.state["task"]}],
        )
        contract = {"task_id": "planning-task", "revision": 1, "body": body()}
        contract.update(hash=util.digest(contract), approval_status="draft", approval_event=None)
        self.state["goal_contract"] = contract
        source_log.seed(self.state)
        public = self.legacy(
            pause="PAUSED_PLANNING_BUDGET", budget=disposition.budget(self.state, "PAUSED_PLANNING_BUDGET")
        )
        entry = self.state["resolver"]["human_escalations"][public["request_id"]]
        original = entry["identity"]["proposal"]["evidence"]["resolver_receipt_id"]
        if queued:
            self.state[human.PRIVATE] = copy.deepcopy(entry["identity"]["proposal"])
            del self.state["resolver"]["human_escalations"][public["request_id"]]
            self.state.pop(human.PUBLIC, None)
            self.state.pop("user_request", None)
            self.state.update(status="RESOLVER_PENDING", pending_questions=[])
        return original

    def committed_feedback(self):
        original = self.planning_incident()
        args = Arguments(feedback="Keep the greeting local", resume_paused=True)
        decided = actions.Decided()
        writer = self.writer()
        self.assertIsNone(
            admission.guard(writer, args, None, self.state, self.run / "state.json", self.run, self.root, decided)
        )
        writer.reconcile_active = lambda *_: self.fail("input selection cannot reconcile or replay an attempt")
        self.assertIsNone(
            actions.action_recovery.resume(
                writer, args, None, self.state, self.run / "state.json", self.run, self.root, decided
            )
        )
        # The real domain handler commits its paired event/source ledgers. This
        # fixture disables only optional chat interpretation, never admission.
        goals.feedback(self.state, args.feedback, interpret=False)
        writer.write_json(self.run / "state.json", self.state)
        return original, writer, args, decided

    def reserve_incident(self):
        """The actual legacy planning-cap frontier with two collected timeout archives."""
        self.state.pop("active_stage")
        self.state.update(next_stage="review_plan", task_id="planning-task", user_events=[])
        contract = {"task_id": "planning-task", "revision": 1, "body": body()}
        contract.update(hash=util.digest(contract), approval_status="draft", approval_event=None)
        self.state["goal_contract"] = contract
        self.state["settings"].update(joint_planning=True)
        discovery = self.run / "discovery.json"
        support.atomic_json(discovery, {"summary": "accepted repaired discovery"})
        self.state["stages"] = [
            {
                "stage": "draft_plan_report_repair",
                "original_stage": "draft_plan",
                "report_only": True,
                "exit_code": 0,
                "output": str(discovery),
            }
        ]
        self.state["planning"] = {
            "plan_review_calls": 2,
            "reports": {"draft_plan": {"output": str(discovery), "report": {"summary": "accepted repaired discovery"}}},
        }
        snapshot = {"revision": "source-revision", "files": {}}
        for number in (1, 2):
            directory = self.run / f"archived-{number}"
            directory.mkdir()
            for name in ("before", "after"):
                support.atomic_json(directory / f"{name}.json", snapshot)
            events = directory / "events.jsonl"
            events.write_text('{"type":"step_start"}\n')
            self.state["stages"].append(
                {
                    "stage": "review_plan",
                    "role": "task_planner",
                    "iteration": number,
                    "output": str(directory / "report.json"),
                    "events": str(events),
                    "before_ref": str(directory / "before.json"),
                    "after_ref": str(directory / "after.json"),
                    "source_revision": snapshot["revision"],
                    "changed_files": [],
                    "accounted": True,
                    "timed_out": True,
                    "automatic_recovery": True,
                    "abandoned": True,
                    "rejected": True,
                    "exit_code": 1,
                }
            )
            self.state.setdefault("reconciliation_notes", []).append(
                {"archived": str(directory), "stage": "review_plan", "iteration": number}
            )
        public = self.legacy(
            pause="PAUSED_PLANNING_BUDGET", budget=disposition.budget(self.state, "PAUSED_PLANNING_BUDGET")
        )
        self.enterContext(patch.object(runtime.source_scope, "snapshot", return_value=snapshot))
        return public, self.recovery_writer()

    def test_actual_bounded_legacy_reconsideration_keeps_counts_and_revalidates_at_final_admission(self):
        public, writer = self.reserve_incident()
        original = self.state["resolver"]["human_escalations"][public["request_id"]]["identity"]["proposal"][
            "evidence"
        ]["resolver_receipt_id"]
        self.assertIsNone(human.current(self.state))
        self.assertEqual(public, human.recovery_request(self.state))
        args, decided = Arguments(resume_paused=True), actions.Decided()
        self.assertIsNone(
            admission.guard(writer, args, None, self.state, self.run / "state.json", self.run, self.root, decided)
        )
        self.assertEqual("planning_reserve", decided.operational_selection)
        self.assertEqual("superseded", self.state["resolver"]["human_escalations"][public["request_id"]]["status"])
        self.assertEqual(2, self.state["planning"]["plan_review_calls"])
        self.assertEqual(1, len(self.state["planning"]["recovery_review_grants"]))
        self.assertTrue(admission.ready(self.state, decided))
        self.assertEqual(original, disposition.incident_identity(self.state, disposition.held(self.state)))
        counts = copy.deepcopy(self.state["planning"])
        with patch.object(
            writer, "reset_report_repair_for_resume", side_effect=AssertionError("no generic resume reset")
        ):
            import autocode_action_recovery as action_recovery

            self.assertIsNone(
                action_recovery.resume(
                    writer, args, None, self.state, self.run / "state.json", self.run, self.root, decided
                )
            )
        args.expected_goal_token = "stale"
        self.assertEqual(
            2,
            actions._admit_build(writer, args, None, self.state, self.run / "state.json", self.run, self.root, decided),
        )
        self.assertIsNotNone(disposition.held(self.state))
        args.expected_goal_token = None
        self.assertIsNone(
            actions._admit_build(writer, args, None, self.state, self.run / "state.json", self.run, self.root, decided)
        )
        self.assertEqual(counts, self.state["planning"])
        self.assertIsNone(disposition.held(self.state))

    def test_reserve_metadata_cannot_admit_from_changed_ledger_or_tampered_archive(self):
        _, writer = self.reserve_incident()
        decided, args = actions.Decided(), Arguments(resume_paused=True)
        self.assertIsNone(
            admission.guard(writer, args, None, self.state, self.run / "state.json", self.run, self.root, decided)
        )
        pristine = copy.deepcopy(self.state)
        for changed in ("count", "reserve", "foreign_run"):
            with self.subTest(changed=changed):
                self.state = copy.deepcopy(pristine)
                if changed == "count":
                    self.state["planning"]["plan_review_calls"] += 1
                elif changed == "reserve":
                    self.state["planning"]["recovery_review_grants"][0]["binding"]["ordinary_limit"] += 1
                else:
                    self.state["workspace"] += "-foreign"
                self.assertFalse(admission.ready(self.state, actions.Decided()))
        self.state = copy.deepcopy(pristine)
        path = Path(self.state["planning"]["recovery_review_grants"][0]["receipt_output"])
        path.write_text("{}")
        with self.assertRaises(util.Paused):
            actions._admit_build(
                writer, args, None, self.state, self.run / "state.json", self.run, self.root, actions.Decided()
            )
        self.assertIsNotNone(disposition.held(self.state))

    def test_reconsideration_json_uncertainty_never_records_checked_credit_or_drops_the_incident(self):
        _, writer = self.reserve_incident()
        original_write = writer.write_json
        calls = []
        error = util.JSONCommitUncertain(self.run / "state.json", candidate="fixture", replaced=True, durable=False)

        def uncertain(path, value):
            calls.append(path)
            if len(calls) == 2:
                raise error
            original_write(path, value)

        writer.write_json = uncertain
        with self.assertRaises(util.JSONCommitUncertain) as raised:
            admission.guard(
                writer,
                Arguments(resume_paused=True),
                None,
                self.state,
                self.run / "state.json",
                self.run,
                self.root,
                actions.Decided(),
            )
        self.assertIs(error, raised.exception)
        self.assertIsNotNone(disposition.held(self.state))
        self.assertFalse(admission.ready(self.state, actions.Decided()))
        self.assertFalse(self.state["planning"].get("recovery_review_grants"))

    def test_actual_default_planning_feedback_is_retained_until_final_guarded_admission(self):
        self.state.update(automatic_recoveries_since_resume=3)
        original, writer, args, decided = self.committed_feedback()
        self.assertEqual(3, self.state["automatic_recoveries_since_resume"])
        self.assertFalse(decided.operational_resume_deferred)
        self.assertEqual(original, disposition.incident_identity(self.state, disposition.held(self.state)))
        self.assertEqual("planning_input", disposition.decision(self.state)["kind"])
        self.assertTrue(admission.ready(self.state, actions.Decided()))
        args.expected_goal_token = "stale-plan"
        self.assertEqual(
            2,
            actions._admit_build(
                writer, args, None, self.state, self.run / "state.json", self.run, self.root, actions.Decided()
            ),
        )
        self.assertIsNotNone(disposition.held(self.state))
        args.expected_goal_token = None
        self.assertIsNone(
            actions._admit_build(
                writer, args, None, self.state, self.run / "state.json", self.run, self.root, actions.Decided()
            )
        )
        self.assertIsNone(disposition.held(self.state))
        recovered = [
            r for r in self.state["resolver"]["operational_receipts"].values() if r["decision"]["action"] == "recovered"
        ]
        self.assertEqual([original], [r["receipt"]["evidence"]["recovered_receipt_id"] for r in recovered])

    def test_planning_input_must_match_original_contract_and_both_committed_ledgers(self):
        _, _, _, _ = self.committed_feedback()
        valid = copy.deepcopy(self.state)
        for change in ("contract", "brief_feedback", "user_events", "old_workspace", "prior_prefix"):
            with self.subTest(change=change):
                self.state = copy.deepcopy(valid)
                if change == "contract":
                    self.state["user_events"][-1]["contract_token"] = "r0:foreign"
                elif change == "old_workspace":
                    self.state["workspace"] += "/replay"
                elif change == "prior_prefix":
                    self.state["user_events"].insert(0, {"kind": "foreign"})
                else:
                    self.state[change] = []
                self.assertFalse(admission.ready(self.state, actions.Decided()))
                self.assertEqual("blocked", disposition.decision(self.state)["kind"])

    def test_actual_goal_edit_keeps_original_incident_until_its_new_plan_decision(self):
        original = self.planning_incident()
        args = Arguments(edit_goal="reviewed-plan.json", resume_paused=True)
        decided = actions.Decided()
        self.assertIsNone(
            admission.guard(
                self.writer(), args, None, self.state, self.run / "state.json", self.run, self.root, decided
            )
        )
        self.assertIsNone(
            actions.action_recovery.resume(
                self.writer(), args, None, self.state, self.run / "state.json", self.run, self.root, decided
            )
        )
        lifecycle.install_draft(self.state, body(), origin="user_cli_edit")
        self.assertIsNotNone(disposition.planning_input(self.state, disposition.held(self.state)))
        self.assertEqual(original, disposition.incident_identity(self.state, disposition.held(self.state)))
        self.assertFalse(admission.ready(self.state, decided))
        self.assertEqual("planning_input", disposition.decision(self.state)["kind"])

    def test_genuine_new_question_survives_planning_block_but_cannot_admit_a_stage(self):
        original, writer, args, decided = self.committed_feedback()
        lifecycle.install_draft(self.state, body(questions=True), origin="user_cli_edit")
        self.assertEqual("escalate", human.evaluate(self.state))
        shown = run_view.view(self.state, run_dir=self.run, workspace=self.root)
        self.assertEqual("answer", shown["needs"]["kind"])
        self.assertIn("Q1", str(shown["needs"]))
        self.assertNotEqual("resume", shown["needs"]["kind"])
        self.assertFalse(admission.ready(self.state, decided))
        self.assertEqual(
            2,
            actions._admit_build(writer, args, None, self.state, self.run / "state.json", self.run, self.root, decided),
        )
        self.assertEqual(original, disposition.incident_identity(self.state, disposition.held(self.state)))

    def test_planning_input_does_not_hide_or_retire_another_process_incident(self):
        _, writer, args, decided = self.committed_feedback()
        runtime._operational_receipt(
            self.state,
            self.run,
            "blocked",
            disposition.MESSAGE,
            {
                "status": "PAUSED_PROVIDER_UNCERTAIN",
                "stage": "builder",
                "budget": disposition.budget(self.state, "PAUSED_PROVIDER_UNCERTAIN"),
            },
        )
        self.assertEqual("blocked", disposition.decision(self.state)["kind"])
        self.assertIsNotNone(disposition.presentation(self.state))
        self.assertFalse(admission.ready(self.state, decided))
        with self.assertRaises(ValueError):
            admission.retire(writer, self.state, self.run, decided)

    def test_existing_typed_receipts_keep_their_namespace_and_advancement_priority(self):
        pristine = copy.deepcopy(self.state)
        for error in (
            custody.MetadataAdmissionBusy(),
            custody.CustodyUncertain(),
            typed_block.WorkerEvidenceUncertain({"fixture": "owned"}),
        ):
            with self.subTest(type=type(error).__name__):
                self.state = copy.deepcopy(pristine)
                self.assertTrue(typed_block.record(self.state, error, run_dir=self.run, workspace=self.root))
                self.assertEqual([], disposition.incidents(self.state))
                self.assertIsNone(disposition.presentation(self.state))
                with self.assertRaises(typed_block.BlockedOperation):
                    typed_block.guard(self.writer(), self.state, self.run, self.root)
                self.assertFalse(admission.ready(self.state, actions.Decided()))

    def test_retagged_generic_ids_cannot_disappear_into_another_receipt_domain(self):
        pristine = copy.deepcopy(self.state)
        for kind in ("legacy_hold", "blocked", "recovered"):
            with self.subTest(kind=kind):
                self.state = copy.deepcopy(pristine)
                if kind == "legacy_hold":
                    original = self.queued()
                    self.state.pop(human.PRIVATE)
                else:
                    runtime.record_operational_exhaustion(
                        self.runner, self.state, self.run, support.Paused("PAUSED_PROVIDER_UNCERTAIN", "offline")
                    )
                    original = disposition.held(self.state)["receipt"]["id"]
                row = self.state["resolver"]["operational_receipts"][original]
                row["receipt"]["scope"] = "verification_preparation"
                if kind == "recovered":
                    row["decision"]["action"] = "recovered"
                before = copy.deepcopy(self.state)
                self.assertIsNone(receipt_identity.scope(row, original))
                self.assertIsNotNone(disposition.held(self.state))
                shown = run_view.view(self.state, run_dir=self.run, workspace=self.root)
                self.assertIsNone(shown["needs"])
                self.assertIsNone(shown["next_command"])
                self.assertIsNone(shown["chat"]["input"])
                self.assertFalse(admission.ready(self.state, actions.Decided()))
                self.assertEqual(before, self.state)

    def test_actual_other_domain_writers_keep_their_own_readers_and_unchanged_ids(self):
        import autocode_model_listing_failure as listing
        import autocode_model_listing_failure_recovery as listing_recovery
        import autocode_no_progress_recovery as no_progress
        import autocode_operational_block_view as block_view
        import autocode_verification_failure as verification
        import autocode_verification_failure_recovery as verification_recovery

        pristine = copy.deepcopy(self.state)
        for scope, action, prefix in (
            (no_progress.RECOVERY_SCOPE, "continue", "no-progress-proof-recovery"),
            (no_progress.EXHAUSTED_SCOPE, "could_not_finish", "no-progress-exhausted"),
        ):
            with self.subTest(scope=scope):
                self.state = copy.deepcopy(pristine)
                identity = no_progress._append_operational_record(
                    self.state,
                    self.run,
                    scope=scope,
                    action=action,
                    summary="Recorded no-progress decision",
                    evidence={"proof_digest": "proof", "writer_output": "writer"},
                    at=support.now(),
                )
                row = self.state["resolver"]["operational_receipts"][identity]
                self.assertEqual(prefix + "-1", identity)
                self.assertEqual(row, json.loads(Path(row["output"]).read_text()))
                self.assertEqual(scope, receipt_identity.scope(row, identity))
                self.assertEqual([], disposition.incidents(self.state))
                self.assertIsNone(block_view.presentation(self.state))
                self.assertEqual(
                    scope == no_progress.RECOVERY_SCOPE,
                    no_progress.proof_already_spent(self.state, {"proof_digest": "proof"}),
                )

        self.state = copy.deepcopy(pristine)
        error = listing.ModelListingTimeout("Listing stopped", workspace=self.root, config_sha256="a" * 64)
        self.assertTrue(listing_recovery.settle(self.writer(), self.state, self.run, self.root, error))
        self.assertEqual("model-listing-1", listing.blocked(self.state)["receipt"]["id"])
        self.assertEqual([], disposition.incidents(self.state))
        self.assertIsNotNone(listing.presentation(self.state))

        self.state = copy.deepcopy(pristine)
        observation = verification.GitPreparationFailed(
            subprocess.CalledProcessError(1, ["git", "read-tree"]),
            workspace=self.root,
            run_dir=self.run,
            control=self.run / "check",
            source_revision="source",
            source_paths=[],
            cleanup_confirmed=False,
            cleanup_error="Unconfirmed fixture cleanup",
        )
        with self.assertRaises(util.Paused):
            verification_recovery.recover(
                self.writer(),
                self.state,
                self.run,
                self.root,
                verification.PrelaunchFailure("Git stopped", observation),
            )
        self.assertEqual("verification-preparation-1", verification.blocked(self.state)["receipt"]["id"])
        self.assertEqual([], disposition.incidents(self.state))
        self.assertIsNotNone(verification.presentation(self.state))

    def test_public_planning_hold_reads_long_history_with_one_linear_prefix_scan_per_lookup(self):
        self.planning_incident()
        self.state["user_events"].extend(
            {"kind": "recorded_observation", "id": f"event-{index}", "payload": "x" * 30} for index in range(1024)
        )
        canonical = util._canonical_json
        with patch.object(util, "digest_prefix_length", wraps=util.digest_prefix_length) as scans:
            with patch.object(util, "_canonical_json", wraps=canonical) as encoded:
                shown = run_view.view(self.state, run_dir=self.run, workspace=self.root)
        self.assertGreater(scans.call_count, 0)
        # Each exact-prefix lookup serializes every event once. Other digest
        # calls authenticate whole saved records; none scans all prefix lists.
        event_values = [
            call.args[0]
            for call in encoded.call_args_list
            if isinstance(call.args[0], dict) and call.args[0].get("kind") == "recorded_observation"
        ]
        self.assertEqual(1024 * scans.call_count, len(event_values))
        self.assertIsNone(shown["needs"])
        self.assertIsNone(shown["chat"]["input"])
        self.assertEqual("blocked", shown["recovery"]["category"])

    def test_queued_feedback_is_applied_under_hold_and_rederived_on_a_later_admit(self):
        original = self.planning_incident()
        self.run.mkdir(parents=True, exist_ok=True)
        request = {
            "id": "owner-feedback",
            "kind": "feedback",
            "text": "Keep the greeting local",
            "order": 1,
            "submitted_at": support.now(),
            "observed_goal_token": goals.token(self.state["goal_contract"]),
            "boundary_pause_requested": True,
        }
        support.atomic_json(self.run / interventions.INBOX_NAME, {"version": 1, "requests": [request]})
        writer = self.writer()
        writer.consume_interventions = lambda state, run, workspace, **kw: stop.consume(
            state, run, workspace, write_json=writer.write_json, now=writer.now, **kw
        )
        with patch.object(
            human, "pending_interruptions", return_value={"pending": [request], "pause_requested": False}
        ):
            self.assertEqual(
                2,
                admission.guard(
                    writer,
                    Arguments(resume_paused=True),
                    None,
                    self.state,
                    self.run / "state.json",
                    self.run,
                    self.root,
                    actions.Decided(),
                ),
            )
        self.assertEqual([], interventions.pending(self.run))
        self.assertEqual("owner-feedback", self.state["applied_interventions"][0]["id"])
        self.assertEqual(original, disposition.incident_identity(self.state, disposition.held(self.state)))
        self.assertEqual("planning_input", disposition.decision(self.state)["kind"])
        self.assertFalse(admission.ready(self.state, actions.Decided()))

    def actual_grant(self, *, checked=True):
        self.state.pop("active_stage")
        self.state.update(
            automatic_recoveries_since_resume=3,
            consecutive_timeout_recoveries=3,
            automatic_timeout_recoveries=[{"stage": "requirements_gather"} for _ in range(3)],
        )
        original = self.queued(pause="PAUSED_TIMEOUT_RECOVERY")
        decided, writer = actions.Decided(), self.writer()
        args = Arguments(resume_paused=True, grant_recovery=1)
        self.assertIsNone(
            admission.guard(writer, args, None, self.state, self.run / "state.json", self.run, self.root, decided)
        )
        result = recovery_grants.grant(
            self.state,
            self.run,
            1,
            previous_settings=None,
            current_request=human.current,
            queued_request=human.queued_operational,
            count=3,
            supersede=human.supersede_operational,
            persist=writer.write_json,
            maximum=3,
        )
        self.assertFalse(
            admission.ready(self.state, decided), "a grant ledger alone cannot select the original incident"
        )
        if checked:
            admission.checked(writer, self.state, self.run, decided, "grant", result)
            self.assertTrue(admission.ready(self.state, actions.Decided()))
        return original, writer, decided, result

    def test_checked_grant_survives_queued_pause_and_repeats_final_guards_without_a_second_grant(self):
        original, writer, _, _ = self.actual_grant()
        request = {
            "id": "owner-pause",
            "kind": "pause",
            "text": "",
            "order": 1,
            "submitted_at": support.now(),
            "observed_goal_token": None,
            "boundary_pause_requested": True,
        }
        support.atomic_json(self.run / interventions.INBOX_NAME, {"version": 1, "requests": [request]})
        self.assertTrue(
            stop.consume(self.state, self.run, self.root, write_json=writer.write_json, now=writer.now, released=True)
        )
        self.assertIsNotNone(disposition.held(self.state), "a metadata return cannot retire the incident")
        self.assert_blocked_status()
        args, decided = Arguments(resume_paused=True), actions.Decided()
        self.assertIsNone(
            admission.guard(writer, args, None, self.state, self.run / "state.json", self.run, self.root, decided)
        )
        writer.recover_legacy_report_repair = lambda *_: False
        writer.migrate_opencode_roles = lambda *_: False
        writer.workflow = SimpleNamespace(review_stage=lambda _: "tester")
        args.expected_goal_token = "r0:stale"
        self.assertEqual(
            2, actions.admit(writer, args, None, self.state, self.run / "state.json", self.run, self.root, decided)
        )
        self.assertIsNotNone(disposition.held(self.state))
        self.assertEqual(1, len(self.state["recovery_grants"]))
        args.expected_goal_token = None
        self.assertIsNone(
            actions.admit(writer, args, None, self.state, self.run / "state.json", self.run, self.root, decided)
        )
        self.assertIsNone(disposition.held(self.state))
        self.assertEqual(2, self.state["automatic_recoveries_since_resume"])
        self.assertEqual(1, len(self.state["recovery_grants"]))
        self.assertIn(original, self.state["resolver"]["operational_receipts"])

    def assert_blocked_status(self):
        shown = run_view.view(self.state, run_dir=self.run, workspace=self.root)
        self.assertIsNone(shown["needs"])
        self.assertIsNone(shown["next_command"])
        self.assertIsNone(shown["chat"]["input"])
        self.assertEqual("blocked", shown["recovery"]["category"])

    def test_checked_diagnostic_requires_unchanged_own_handler_ledger_and_run(self):
        self.actual_grant()
        pristine = copy.deepcopy(self.state)
        for change in ("grant", "event", "counter", "settings", "workspace", "diagnostic", "missing_ledger"):
            with self.subTest(change=change):
                self.state = copy.deepcopy(pristine)
                if change == "grant":
                    self.state["recovery_grants"][-1]["amount"] = 2
                elif change == "event":
                    self.state["user_events"][-1]["previous_count"] = 9
                elif change == "counter":
                    self.state["automatic_recoveries_since_resume"] = 1
                elif change == "settings":
                    self.state["settings"]["limits"]["max_cost_usd"] = 9
                elif change == "workspace":
                    self.state["workspace"] += "/foreign"
                elif change == "diagnostic":
                    identity = self.state["resolver"]["operational_diagnostics"][-1]
                    self.state["resolver"]["operational_receipts"][identity]["receipt"]["evidence"][
                        "original_receipt_id"
                    ] = "foreign"
                else:
                    self.state["recovery_grants"] = []
                self.assertFalse(admission.ready(self.state, actions.Decided()))
                self.assertIsNotNone(disposition.held(self.state))

    def test_uncertain_checked_diagnostic_writes_never_grant_current_invocation_authority(self):
        _, _, selected, result = self.actual_grant(checked=False)
        pristine = copy.deepcopy(self.state)
        for boundary in ("diagnostic", "state_before_replace", "state_after_replace"):
            with self.subTest(boundary=boundary):
                self.state = copy.deepcopy(pristine)
                writer = self.writer()
                decided = actions.Decided(
                    operational_incident=selected.operational_incident, operational_selection="grant"
                )
                before = copy.deepcopy(self.state)
                error = util.JSONCommitUncertain(
                    self.run / "state.json",
                    candidate="fixture",
                    replaced=boundary == "state_after_replace",
                    durable=False,
                )
                if boundary == "diagnostic":
                    with patch.object(support, "atomic_json", side_effect=error):
                        with self.assertRaises(util.JSONCommitUncertain):
                            admission.checked(writer, self.state, self.run, decided, "grant", result)
                else:

                    def failed_save(path, candidate):
                        if boundary == "state_after_replace":
                            support.atomic_json(path, candidate)
                        raise error

                    writer.write_json = failed_save
                    with self.assertRaises(util.JSONCommitUncertain):
                        admission.checked(writer, self.state, self.run, decided, "grant", result)
                self.assertEqual(before, self.state)
                self.assertFalse(admission.ready(self.state, decided))
                self.assertIsNone(decided.operational_recovery_checked)

    def test_queued_milestone_metadata_retains_sole_incident_without_dispatch_credit(self):
        self.state.pop("active_stage")
        original = self.queued()
        request = {"version": 1, "actor": "user_cli", "id": "owner-milestones", "max_seconds": 120}
        support.atomic_json(self.run / "milestone-checkpoints-requested.json", request)
        (self.run / "pause-requested").write_text("milestone-checkpoints:owner-milestones")
        self.assertEqual(
            2,
            admission.guard(
                self.writer(),
                Arguments(resume_paused=True),
                None,
                self.state,
                self.run / "state.json",
                self.run,
                self.root,
                actions.Decided(),
            ),
        )
        self.assertTrue(self.state["settings"]["milestone_checkpoints"])
        self.assertFalse((self.run / "pause-requested").exists())
        self.assertEqual(original, disposition.incident_identity(self.state, disposition.held(self.state)))
        self.assertFalse(admission.ready(self.state, actions.Decided()))

    def test_existing_bound_handler_keeps_exact_owner_change_and_survives_a_metadata_return(self):
        self.state.pop("active_stage")
        self.state["settings"]["limits"]["max_seconds"] = 1
        self.state["settings"]["budget_origins"] = {"max_seconds": "runner_default"}
        self.legacy(pause="PAUSED_TIME_LIMIT", budget=disposition.budget(self.state, "PAUSED_TIME_LIMIT"))
        previous = copy.deepcopy(self.state["settings"])
        self.state["settings"]["limits"]["max_seconds"] = 5
        self.state["settings"]["budget_origins"]["max_seconds"] = "user_explicit"
        limit_events.record(self.state, previous, self.state["settings"], at=support.now())
        args, decided, writer = (
            Arguments(resume_paused=True, _explicit_budget_flags={"max_seconds"}),
            actions.Decided(),
            self.recovery_writer(),
        )
        self.assertIsNone(
            admission.guard(writer, args, None, self.state, self.run / "state.json", self.run, self.root, decided)
        )
        self.assertFalse(admission.ready(self.state, decided), "a settings save and flag are not handler proof")
        self.assertIsNone(
            actions.action_recovery.resume(
                writer, args, None, self.state, self.run / "state.json", self.run, self.root, decided
            )
        )
        self.assertTrue(admission.ready(self.state, actions.Decided()))
        self.assertIsNotNone(disposition.held(self.state), "checked metadata still does not retire")
        saved = copy.deepcopy(self.state)
        self.state["settings"]["limits"]["max_seconds"] = 6
        self.assertFalse(admission.ready(self.state, actions.Decided()))
        self.state = saved
        self.state["user_events"][0]["actor"] = "model"
        self.assertFalse(
            admission.ready(self.state, actions.Decided()), "a changed ledger is not the checked owner change"
        )

    def test_existing_serial_builder_handler_validates_list_selector_before_checked_credit(self):
        self.state.pop("active_stage")
        self.state.update(
            next_stage="builder", current_task={"id": "task-M1", "milestone_id": "M1", "kind": "implement"}
        )
        self.state["settings"].update(
            builder_retry={"enabled": True}, roles={"builder": {"model": "saved-model", "reasoning_effort": "high"}}
        )
        key = builder_policy.key(self.state)
        self.state["builder_retries"] = {key: {"action": "pause", "failures": [{"cause": "retained failure"}]}}
        self.legacy(pause="PAUSED_BUILDER_RETRY_LIMIT")
        args, decided, writer = Arguments(resume_paused=True, retry_builder=["M1"]), actions.Decided(), self.writer()
        self.assertIsNone(
            admission.guard(writer, args, None, self.state, self.run / "state.json", self.run, self.root, decided)
        )
        with self.assertRaises(ValueError):
            builder_recovery.request_serial_retry(self.state, ["M-foreign"])
        self.assertFalse(admission.ready(self.state, decided))
        builder_recovery.request_serial_retry(self.state, args.retry_builder)
        admission.checked(writer, self.state, self.run, decided, "builder", {"milestone": args.retry_builder})
        self.assertTrue(admission.ready(self.state, actions.Decided()))
        self.assertEqual("saved-model", self.state["settings"]["roles"]["builder"]["model"])
        self.state["builder_retry_decisions"][-1]["owner"] = "model"
        self.assertFalse(admission.ready(self.state, actions.Decided()))

    def test_existing_report_handler_requires_exact_rejected_attempt_and_retained_archive(self):
        self.state.pop("active_stage")
        self.state["next_stage"] = "tester"
        self.state["settings"]["report_repair"] = {"max_attempts": 2}
        self.run.mkdir(parents=True, exist_ok=True)
        schema = self.run / "tester.schema.json"
        schema.write_text("{}")
        revision = "a" * 40
        original = {
            "stage": "tester",
            "role": "tester",
            "iteration": 1,
            "output": str(self.run / "tester.json"),
            "schema": str(schema),
            "source_revision": revision,
        }
        repair = {
            **original,
            "stage": "tester_report_repair",
            "original_stage": "tester",
            "report_only": True,
            "rejected": True,
            "output": str(self.run / "tester-repair.json"),
        }
        self.state["stages"].extend([original, repair])
        self.state["pending_report_repair"] = {
            "original": original,
            "attempts": 2,
            "latest_rejected": repair,
            "pins": {},
            "contract_hash": None,
            "error": "Check is not supported by an exact executed Validator event",
        }
        self.legacy(pause="PAUSED_REPEATED_FAILURE")
        selected = stage_recovery.records.attempt_id(repair)
        args, decided, writer = Arguments(resume_paused=True, retry_report=selected), actions.Decided(), self.writer()
        self.assertIsNone(
            admission.guard(writer, args, None, self.state, self.run / "state.json", self.run, self.root, decided)
        )
        with patch.object(stage_recovery.source_scope, "snapshot", return_value={"revision": revision}):
            with self.assertRaises(ValueError):
                stage_recovery.retry_format_failed_report(self.state, self.run, self.root, selected + "-foreign")
            self.assertFalse(admission.ready(self.state, decided))
            stage_recovery.retry_format_failed_report(self.state, self.run, self.root, selected)
        admission.checked(writer, self.state, self.run, decided, "report", {"attempt": selected})
        self.assertTrue(admission.ready(self.state, actions.Decided()))
        self.assertEqual(2, self.state["report_repair_archive"][-1]["repair"]["attempts"])
        self.state["report_repair_archive"][-1]["repair"]["attempts"] = 0
        self.assertFalse(admission.ready(self.state, actions.Decided()))

    def test_queued_only_abandonment_cannot_erase_or_retire_the_incident(self):
        original = self.queued()
        args = Arguments(abandon_stage="001/requirements", _explicit_budget_flags=set())
        decided = actions.Decided()
        self.assertIsNone(
            admission.guard(
                self.writer(), args, None, self.state, self.run / "state.json", self.run, self.root, decided
            )
        )
        self.assertTrue(human.withdraw_queued_operational(self.state, "requirements_gather"))
        self.assertEqual(original, disposition.incident_identity(self.state, disposition.held(self.state)))
        self.assertFalse(admission.ready(self.state, decided))
        with self.assertRaises(ValueError):
            admission.retire(self.writer(), self.state, self.run, decided)

    def abandon_incident(self):
        self.state.update(next_stage="builder", sessions={"builder": "retained-session"}, active_seconds=0)
        self.state["settings"].update(
            roles={"builder": {"model": "saved-custom-model", "reasoning_effort": "high"}},
            regression={"test_command": "python -m unittest"},
        )
        base = self.run / "builder-01"
        base.parent.mkdir(parents=True, exist_ok=True)
        before = {"revision": "before", "files": {}}
        after = {"revision": "after", "files": {"partial.py": "retained-bytes"}}
        support.atomic_json(base.with_suffix(".before.json"), before)
        base.with_suffix(".jsonl").write_text('{"type":"thread.started","thread_id":"retained-session"}\n')
        self.state["active_stage"] = {
            "stage": "builder",
            "role": "builder",
            "iteration": 1,
            "duration_seconds": 8,
            "exit_code": -15,
            "output": str(base.with_suffix(".json")),
            "events": str(base.with_suffix(".jsonl")),
            "before_ref": str(base.with_suffix(".before.json")),
        }
        original = self.queued()
        self.enterContext(patch.object(stage_recovery.source_scope, "snapshot", return_value=after))
        return original, self.recovery_writer(), "001/builder-01"

    def checked_abandon(self):
        import autocode_action_recovery as action_recovery

        original, writer, selected = self.abandon_incident()
        args, decided = Arguments(abandon_stage=selected), actions.Decided()
        self.assertIsNone(
            admission.guard(writer, args, None, self.state, self.run / "state.json", self.run, self.root, decided)
        )
        self.assertEqual(
            0,
            action_recovery.resume(
                writer, args, None, self.state, self.run / "state.json", self.run, self.root, decided
            ),
        )
        return original, writer, decided

    def test_actual_abandon_settlement_survives_metadata_return_then_plain_resume_without_renewal(self):
        import autocode_action_recovery as action_recovery

        original, writer, _ = self.checked_abandon()
        self.assertEqual("PAUSED_STAGE_ABANDONED", self.state["status"])
        self.assertNotIn("active_stage", self.state)
        self.assertEqual(8, self.state["active_seconds"])
        self.assertNotIn("builder", self.state["sessions"])
        archived = next(row for row in self.state["stages"] if row.get("abandoned"))
        self.assertIsNone(archived["metrics"]["provider_tokens"]["input_tokens"])
        self.assertIsNone(archived["metrics"]["provider_tokens"]["output_tokens"])
        self.assertEqual(original, disposition.incident_identity(self.state, disposition.held(self.state)))
        settings = copy.deepcopy(self.state["settings"])
        args, fresh = Arguments(resume_paused=True), actions.Decided()
        self.assertIsNone(
            admission.guard(writer, args, None, self.state, self.run / "state.json", self.run, self.root, fresh)
        )
        with patch.object(writer, "reset_report_repair_for_resume", side_effect=AssertionError("no renewal")):
            self.assertIsNone(
                action_recovery.resume(
                    writer, args, None, self.state, self.run / "state.json", self.run, self.root, fresh
                )
            )
            self.assertFalse(fresh.operational_resume_deferred)
            self.assertIsNone(
                actions._admit_build(
                    writer, args, None, self.state, self.run / "state.json", self.run, self.root, fresh
                )
            )
        self.assertEqual(settings, self.state["settings"])
        self.assertEqual(8, self.state["active_seconds"])
        self.assertIsNone(disposition.held(self.state))

    def test_abandon_checked_fact_requires_exact_retained_record_context_counts_and_archive_bytes(self):
        _, writer, _ = self.checked_abandon()
        pristine = copy.deepcopy(self.state)
        for change in ("record", "context", "time", "history", "foreign_run", "fake_event"):
            with self.subTest(change=change):
                self.state = copy.deepcopy(pristine)
                if change == "record":
                    next(row for row in self.state["stages"] if row.get("abandoned"))["metrics"]["provider_tokens"][
                        "input_tokens"
                    ] = 0
                elif change == "context":
                    self.state["recovery_context"]["source_revision"] = "unrelated"
                elif change == "time":
                    self.state["active_seconds"] = 0
                elif change == "history":
                    self.state["report_repair_archive"] = [{"repair": {"attempts": 0}}]
                elif change == "foreign_run":
                    self.state["run_dir"] += "-foreign"
                else:
                    self.state["user_events"][-1]["attempt_id"] = "001/foreign"
                self.assertFalse(admission.ready(self.state, actions.Decided()))
        self.state = copy.deepcopy(pristine)
        archived = next(row for row in self.state["stages"] if row.get("abandoned"))
        Path(archived["events"]).write_text('{"type":"turn.completed"}\n')
        with self.assertRaises(ValueError):
            actions._admit_build(
                writer,
                Arguments(resume_paused=True),
                None,
                self.state,
                self.run / "state.json",
                self.run,
                self.root,
                actions.Decided(),
            )
        self.assertIsNotNone(disposition.held(self.state))

    def test_uncertain_actual_abandon_state_commit_never_emits_checked_settlement(self):
        import autocode_action_recovery as action_recovery

        original, writer, selected = self.abandon_incident()
        pristine = copy.deepcopy(self.state)
        real_write = stage_recovery.records.write_json
        for replaced in (False, True):
            with self.subTest(replaced=replaced):
                self.state = copy.deepcopy(pristine)
                args, decided = Arguments(abandon_stage=selected), actions.Decided()
                self.assertIsNone(
                    admission.guard(
                        writer, args, None, self.state, self.run / "state.json", self.run, self.root, decided
                    )
                )
                error = util.JSONCommitUncertain("fixture", candidate="state", replaced=replaced, durable=False)

                def uncertain(path, value):
                    if Path(path).name == "state.json":
                        if replaced:
                            real_write(path, value)
                        raise error
                    real_write(path, value)

                with patch.object(stage_recovery.records, "write_json", side_effect=uncertain):
                    with self.assertRaises(util.JSONCommitUncertain) as raised:
                        action_recovery.resume(
                            writer, args, None, self.state, self.run / "state.json", self.run, self.root, decided
                        )
                self.assertIs(error, raised.exception)
                self.assertEqual(original, disposition.incident_identity(self.state, disposition.held(self.state)))
                self.assertFalse(admission.ready(self.state, actions.Decided()))
                self.assertIsNone(decided.operational_recovery_checked)

    def test_uncertain_normalization_commit_retains_original_queue_and_no_credit(self):
        self.queued()
        before = copy.deepcopy(self.state)
        writer = self.writer()
        error = util.JSONCommitUncertain(self.run / "state.json", candidate="fixture", replaced=True, durable=False)
        writer.write_json = lambda *_: (_ for _ in ()).throw(error)
        decided = actions.Decided()
        with self.assertRaises(util.JSONCommitUncertain) as raised:
            admission.guard(
                writer,
                Arguments(abandon_stage="001/requirements"),
                None,
                self.state,
                self.run / "state.json",
                self.run,
                self.root,
                decided,
            )
        self.assertIs(error, raised.exception)
        self.assertEqual(before, self.state)
        self.assertFalse(admission.ready(self.state, decided))

    def test_wrong_incident_or_stage_handler_return_never_grants_admission(self):
        self.queued(pause="PAUSED_REPEATED_FAILURE")
        decided = actions.Decided()
        args = Arguments(resume_paused=True, retry_failed_stage=True, _explicit_budget_flags=set())
        self.assertIsNone(
            admission.guard(
                self.writer(), args, None, self.state, self.run / "state.json", self.run, self.root, decided
            )
        )
        admission.checked(self.writer(), self.state, self.run, decided, "failure_retry", {"stage": "builder"})
        self.assertFalse(admission.ready(self.state, decided))
        self.assertIsNone(decided.operational_recovery_checked)

    def checked_retry(self):
        self.state.pop("active_stage")
        revision = "a" * 40
        for index in range(3):
            row = {
                "stage": "requirements_gather",
                "iteration": 1,
                "role": "requirements",
                "output": str(self.run / f"requirements-{index}.json"),
                "source_revision": revision,
                "rejected": True,
            }
            failures.record(self.state, row, ValueError("invalid report"), support.now())
            self.state["stages"].append(row)
        original = self.queued(pause="PAUSED_REPEATED_FAILURE")
        decided = actions.Decided()
        args = Arguments(resume_paused=True, retry_failed_stage=True, _explicit_budget_flags=set())
        writer = self.writer()
        self.assertIsNone(
            admission.guard(writer, args, None, self.state, self.run / "state.json", self.run, self.root, decided)
        )
        with patch.object(stage_recovery.source_scope, "snapshot", return_value={"revision": revision}):
            proof = stage_recovery.authorize_failure_retry(self.state, self.run, self.root)
        admission.checked(self.writer(), self.state, self.run, decided, "failure_retry", proof)
        self.assertTrue(admission.ready(self.state, decided))
        return writer, args, decided, original

    def test_actual_scoped_retry_and_final_guards_retire_only_the_exact_original_pair(self):
        writer, args, decided, original = self.checked_retry()
        before_guards = copy.deepcopy(self.state)
        args.expected_goal_token = "stale-plan"
        self.assertEqual(
            2,
            actions._admit_build(writer, args, None, self.state, self.run / "state.json", self.run, self.root, decided),
        )
        self.assertEqual(before_guards, self.state)
        args.expected_goal_token = None
        self.assertIsNone(
            actions._admit_build(writer, args, None, self.state, self.run / "state.json", self.run, self.root, decided)
        )
        self.assertIsNone(disposition.held(self.state))
        records = self.state["resolver"]["operational_receipts"]
        self.assertIn(original, records)
        self.assertTrue(
            any(
                (row["receipt"].get("evidence") or {}).get("original_receipt_id") == original
                for row in records.values()
            )
        )
        recovered = [row for row in records.values() if row["decision"]["action"] == "recovered"]
        self.assertEqual([original], [row["receipt"]["evidence"]["recovered_receipt_id"] for row in recovered])

    def test_saved_spending_and_unknown_origin_bounds_keep_their_original_controls(self):
        pristine = copy.deepcopy(self.state)
        for status, kind in (
            ("PAUSED_COST_LIMIT", "max_cost_usd"),
            ("PAUSED_PLANNING_CALL_LIMIT", "planning_calls"),
            ("PAUSED_PLANNING_BUDGET", "planning_review_call_limit"),
            ("PAUSED_TIME_LIMIT", "max_seconds"),
            ("PAUSED_RESOLVER_OPERATIONAL", "iteration_ceiling"),
        ):
            for origin in ("user_explicit", "unknown"):
                with self.subTest(status=status, kind=kind, origin=origin):
                    self.state = copy.deepcopy(pristine)
                    public = self.legacy(
                        pause=status,
                        budget={"kind": kind, "origin": origin, "limit": 1, "category": "operational_recovery"},
                    )
                    before = copy.deepcopy(self.state)
                    self.assertEqual(public, human.current(self.state))
                    self.assertIsNone(disposition.presentation(self.state))
                    self.assertEqual(before, self.state)

    def test_every_stage_backend_retains_failure_without_asking_about_machinery(self):
        pristine = copy.deepcopy(self.state)
        for stage in (
            "requirements_gather",
            "draft_plan",
            "review_plan",
            "builder",
            "tester",
            "review_completion",
            "write_acceptance_tests",
            "orchestrator",
        ):
            for engine, provider in (
                ("codex", None),
                ("opencode", "opencode"),
                ("opencode", "kilocode"),
                ("opencode", "claude"),
            ):
                with self.subTest(stage=stage, engine=engine, provider=provider):
                    self.state = copy.deepcopy(pristine)
                    self.state["next_stage"] = stage
                    self.state["active_stage"].update(stage=stage, engine=engine, provider=provider)
                    self.state["settings"].update(engine=engine, provider=provider)
                    original = copy.deepcopy(self.state["active_stage"])
                    settings = copy.deepcopy(self.state["settings"])
                    self.assertTrue(
                        runtime.record_operational_exhaustion(
                            self.runner,
                            self.state,
                            self.run,
                            support.Paused("PAUSED_PROVIDER_UNCERTAIN", "offline failure"),
                        )
                    )
                    self.assertEqual(original, self.state["active_stage"])
                    self.assertEqual(settings, self.state["settings"])
                    self.assertIsNone(human.current(self.state))
                    self.assertFalse(self.state.get("pending_questions"))
                    self.assertIsNotNone(disposition.held(self.state))

    def test_forged_diagnostic_cannot_enable_a_reply_or_recovery(self):
        public = self.legacy()
        source = self.state["resolver"]["human_escalations"][public["request_id"]]["identity"]["proposal"]["evidence"][
            "resolver_receipt_id"
        ]
        self.state["resolver"]["operational_receipts"][source]["receipt"]["evidence"]["stage"] = "foreign"
        before = copy.deepcopy(self.state)
        with self.assertRaises(ValueError):
            human.respond_operational(
                self.state, public["request_id"], public["request_token"], "provide_information", "fixed"
            )
        self.assertEqual(
            2,
            admission.guard(
                self.writer(),
                Arguments(resume_paused=True, retry_failed_stage=True),
                None,
                self.state,
                self.run / "state.json",
                self.run,
                self.root,
                actions.Decided(),
            ),
        )
        self.assertEqual(before, self.state)

    def test_saved_route_and_genuine_contract_questions_remain_answerable(self):
        route = {
            "id": "route-builder",
            "route_role": "builder",
            "category": "quota",
            "question": "Which configured model?",
            "options": [],
            "proposed_default": "",
        }
        public = self.legacy(pause="PAUSED_BUDGET", questions=[route])
        self.assertEqual(public, human.current(self.state))
        self.assertIsNone(disposition.presentation(self.state))
        self.state.pop(human.PUBLIC)
        self.state.pop("user_request")
        self.state["pending_questions"] = []
        self.state["stages"].append(
            {"stage": "resolve", "output": "diagnosis.json", "exit_code": 0, "changed_files": []}
        )
        human.queue(
            self.state,
            "blocker",
            {"stage": "resolve"},
            request={
                "kind": "blocker",
                "decision_needed": "Which requirement takes precedence?",
                "impact": "The requirements conflict",
            },
            evidence={"diagnosis": "contract", "output": "diagnosis.json"},
        )
        self.assertEqual("escalate", human.evaluate(self.state))
        self.assertEqual("blocker", human.current(self.state)["scope"])
        self.assertIsNone(disposition.presentation(self.state))

    def test_legacy_scheduled_information_is_not_evaluated_or_published(self):
        _, event, entry = self.consumed()
        frontier = {
            "binding": self.binding(self.state),
            "pause_status": self.state["status"],
            "request_id": event["request_id"],
        }
        self.state["resolver"]["human_response_frontier"] = frontier
        # Construct the exact old scheduling record; current policy remains in
        # force for every operation and assertion below this fixture save.
        with patch.object(information.disposition, "refused", return_value=False):
            self.assertIsNotNone(information.schedule(self.state, event, entry, frontier))
        before = copy.deepcopy(self.state)
        result = information.reevaluate(self.writer(), self.state, self.run, self.root, resume=True)
        self.assertEqual("hold", result.action)
        self.assertIsNone(information.projection(self.state))
        self.assertEqual(before, self.state)

    def test_unchecked_fast_dispatch_cannot_bypass_the_held_incident(self):
        self.legacy()
        original = disposition.incident_identity(self.state, disposition.held(self.state))
        decided = actions.Decided(operational_incident=original)

        def dispatch(*parameters):
            parameters[-1].dispatch = True

        before = copy.deepcopy(self.state)
        with (
            patch.object(actions.action_authority, "prepare_actions", return_value=decided),
            patch.object(actions, "STEPS", (dispatch,)),
        ):
            self.assertEqual(
                2,
                actions.handle(
                    self.writer(), Arguments(), None, self.state, self.run / "state.json", self.run, self.root
                ),
            )
        self.assertEqual(before, self.state)

    def test_stale_runtime_settings_or_missing_pending_do_not_retire_incident(self):
        self.legacy()
        original = disposition.incident_identity(self.state, disposition.held(self.state))
        self.state.pop(human.PUBLIC)
        self.state.pop("user_request")
        self.state["pending_questions"] = []
        self.state["settings"]["limits"]["unrelated"] = 99
        self.state["status"] = "RUNNING"
        before = copy.deepcopy(self.state)
        self.assertEqual(original, disposition.incident_identity(self.state, disposition.held(self.state)))
        self.assertEqual(
            2,
            admission.guard(
                self.writer(),
                Arguments(resume_paused=True),
                None,
                self.state,
                self.run / "state.json",
                self.run,
                self.root,
                actions.Decided(),
            ),
        )
        self.assertEqual(before, self.state)

    def test_old_workspace_recovery_reference_cannot_retire_fresh_replay_incident(self):
        public = self.legacy()
        old = disposition.incident_identity(self.state, disposition.held(self.state))
        self.state.pop("resolver")
        self.state.pop(human.PUBLIC)
        self.state.pop("user_request")
        self.state["pending_questions"] = []
        replay_run = self.root / "replay" / "run"
        self.state.update(run_dir=str(replay_run), workspace=str(replay_run.parent))
        self.state["active_stage"].update(
            events=str(replay_run / "events.jsonl"), output=str(replay_run / "reply.json")
        )
        runtime.record_operational_exhaustion(
            self.runner, self.state, replay_run, support.Paused("PAUSED_PROVIDER_UNCERTAIN", "offline failure")
        )
        current = disposition.incident_identity(self.state, disposition.held(self.state))
        self.assertNotEqual(old, current)
        runtime._operational_receipt(
            self.state,
            replay_run,
            "recovered",
            "A stale recorded reference",
            {
                "recovered_receipt_id": old,
                "status": "PAUSED_PROVIDER_UNCERTAIN",
                "stage": "requirements_gather",
                "budget": disposition.budget(self.state, "PAUSED_PROVIDER_UNCERTAIN"),
            },
        )
        self.assertEqual(current, disposition.incident_identity(self.state, disposition.held(self.state)))
        import autocode_replay_view as replay_view

        events = replay_run / "provider.jsonl"
        events.write_text('{"type":"turn.completed","usage":{}}\n')
        output = replay_run / "provider.json"
        support.atomic_json(output, {"status": "COMPLETE", "summary": "Recorded provider response"})
        self.state["stages"].append(
            {
                "stage": "builder",
                "role": "builder",
                "engine": "codex",
                "iteration": 1,
                "events": str(events),
                "output": str(output),
                "exit_code": 0,
            }
        )
        public_tape = replay_view.project(self.state, run_dir=replay_run)
        self.assertIsNotNone(public_tape)
        self.assertEqual(1, len(public_tape["records"]), "the projection contains a real recorded provider row")
        self.assertEqual(str(events), public_tape["records"][0]["events"])
        self.assertEqual("builder", public_tape["records"][0]["stage"])
        self.assertNotIn("recovered_receipt_id", str(public_tape))
        self.assertNotIn("operational_diagnostic", str(public_tape))
        self.assertNotIn(current, str(public_tape))
        self.assertNotEqual(public["request_id"], current)

    def test_missing_or_malformed_blocked_diagnostic_never_discards_the_hold(self):
        runtime.record_operational_exhaustion(
            self.runner, self.state, self.run, support.Paused("PAUSED_PROVIDER_UNCERTAIN", "offline failure")
        )
        identity = disposition.incident_identity(self.state, disposition.held(self.state))
        original = copy.deepcopy(self.state)
        for broken in ("missing", "changed"):
            with self.subTest(broken=broken):
                self.state = copy.deepcopy(original)
                if broken == "missing":
                    self.state["resolver"]["operational_receipts"].pop(identity)
                else:
                    self.state["resolver"]["operational_receipts"][identity]["receipt"]["evidence"]["stage"] = "foreign"
                before = copy.deepcopy(self.state)
                self.assertIsNotNone(disposition.presentation(self.state))
                self.assertEqual(
                    2,
                    admission.guard(
                        self.writer(),
                        Arguments(resume_paused=True, retry_failed_stage=True),
                        None,
                        self.state,
                        self.run / "state.json",
                        self.run,
                        self.root,
                        actions.Decided(),
                    ),
                )
                self.assertEqual(before, self.state)

    def test_malformed_saved_publication_or_queued_shape_never_hides_the_original_hold(self):
        original = self.queued()
        pristine = copy.deepcopy(self.state)
        for shape in (
            "saved_entry",
            "saved_identity",
            "saved_proposal",
            "saved_container",
            "queued_evidence",
            "queued_origin",
        ):
            with self.subTest(shape=shape):
                self.state = copy.deepcopy(pristine)
                if shape == "saved_entry":
                    self.state["resolver"]["human_escalations"] = {"broken": True}
                elif shape == "saved_identity":
                    self.state["resolver"]["human_escalations"] = {"broken": {"identity": ["bad"]}}
                elif shape == "saved_proposal":
                    self.state["resolver"]["human_escalations"] = {"broken": {"identity": {"proposal": ["bad"]}}}
                elif shape == "saved_container":
                    self.state["resolver"]["human_escalations"] = ["bad"]
                else:
                    self.state[human.PRIVATE]["evidence" if shape == "queued_evidence" else "origin"] = ["bad"]
                self.assertEqual(original, disposition.incident_identity(self.state, disposition.held(self.state)))
                self.assertFalse(admission.ready(self.state, actions.Decided()))
                self.assert_blocked_status()

    def test_stage_model_metadata_preserves_queued_incident_and_next_resume_refusal(self):
        import autocode_action_stage_model as stage_model
        import autocode_interventions as interventions
        import autocode_parent_boundary as parent
        import autocode_planning_artifacts as artifacts
        import autocode_run_records as records

        self.state.pop("active_stage")
        self.state["settings"].update(
            engine="codex",
            roles={
                "builder": {"model": "gpt-5.6-terra", "reasoning_effort": "medium"},
                "tester": {"model": "gpt-5.6-sol", "reasoning_effort": None},
            },
        )
        original = self.queued()
        diagnostics = copy.deepcopy(self.state["resolver"]["operational_diagnostics"])
        writer = self.writer()
        writer.opencode = SimpleNamespace(CONFIGURED=False)
        writer.interventions = interventions
        writer.status_command = SimpleNamespace(reject_input=lambda *_: self.fail("metadata route should be accepted"))
        writer.commit_user_action = lambda state, candidate, run: parent.commit_user_action(
            state,
            candidate,
            run,
            require_current_inputs=False,
            save=records.write_json,
            admission=interventions.admission,
            guard_inputs=lambda *_: self.fail("not an approval"),
            commit_pending=artifacts.commit_pending,
        )
        args = Arguments(stage_model=("builder", "gpt-6-astra", "high"))
        decided = actions.Decided()
        self.assertIsNone(
            admission.guard(writer, args, None, self.state, self.run / "state.json", self.run, self.root, decided)
        )
        self.assertEqual(
            0, stage_model.apply(writer, args, None, self.state, self.run / "state.json", self.run, self.root, decided)
        )
        self.assertEqual("gpt-6-astra", self.state["settings"]["roles"]["builder"]["model"])
        self.assertEqual(
            diagnostics, self.state["resolver"]["operational_diagnostics"], "metadata needs no normalization"
        )
        self.assertEqual(original, disposition.incident_identity(self.state, disposition.held(self.state)))
        before = copy.deepcopy(self.state)
        self.assertEqual(
            2,
            admission.guard(
                writer,
                Arguments(resume_paused=True),
                None,
                self.state,
                self.run / "state.json",
                self.run,
                self.root,
                actions.Decided(),
            ),
        )
        self.assertEqual(before, self.state)
        self.assertEqual(
            2,
            admission.guard(
                writer,
                Arguments(stage_model=args.stage_model, resume_paused=True),
                None,
                self.state,
                self.run / "state.json",
                self.run,
                self.root,
                actions.Decided(),
            ),
        )
        self.assertEqual(before, self.state)

    def test_checked_fast_dispatch_runs_final_guards_without_generic_resume_bookkeeping(self):
        writer, args, decided, original = self.checked_retry()

        def dispatch(*parameters):
            parameters[-1].dispatch = True

        writer.reset_report_repair_for_resume = lambda *_: self.fail("fast dispatch must not reset report repair")
        before = copy.deepcopy(self.state)
        with (
            patch.object(actions.action_authority, "prepare_actions", return_value=decided),
            patch.object(actions, "STEPS", (dispatch,)),
            patch.object(runtime, "reset_for_resume", side_effect=AssertionError("generic resume not admitted")),
        ):
            args.expected_goal_token = "stale-plan"
            self.assertEqual(
                2, actions.handle(writer, args, None, self.state, self.run / "state.json", self.run, self.root)
            )
            self.assertEqual(before, self.state)
            args.expected_goal_token = None
            self.assertIsNone(
                actions.handle(writer, args, None, self.state, self.run / "state.json", self.run, self.root)
            )
        self.assertIsNone(disposition.held(self.state))
        self.assertTrue(
            any(
                row["receipt"].get("evidence", {}).get("recovered_receipt_id") == original
                for row in self.state["resolver"]["operational_receipts"].values()
            )
        )

    def test_unrelated_failure_after_handler_cannot_retire_original_incident(self):
        writer, args, decided, original = self.checked_retry()
        self.state["failure_history"]["unrelated"] = {"count": 1}
        before = copy.deepcopy(self.state)
        self.assertEqual(
            2,
            actions._admit_build(writer, args, None, self.state, self.run / "state.json", self.run, self.root, decided),
        )
        self.assertEqual(before, self.state)
        self.assertEqual(original, disposition.incident_identity(self.state, disposition.held(self.state)))
