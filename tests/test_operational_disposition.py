"""Internal exhaustion cannot create or accept a machinery question; no provider is launched."""

import copy
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import autocode_failures as failures
import autocode_operational_admission as admission
import autocode_operational_disposition as disposition
import autocode_operational_information as information
import autocode_resolver_human as human
import autocode_resolver_runtime as runtime
import autocode_run_actions as actions
import autocode_run_view as run_view
import autocode_stage_recovery as stage_recovery
import autocode_support as support
import autocode_util as util


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
            "failure_history": util.digest(state.get("failure_history", {})),
            "settings": copy.deepcopy(state["settings"]),
            "active_stage": copy.deepcopy(state.get("active_stage")),
            "pending_report_repair": copy.deepcopy(state.get("pending_report_repair")),
            "interruptions": {"pending": [], "pause_requested": False},
            "runtime_sources": "fixture-runtime",
            "private_control": "PRIVATE-FAMILY-ROOT-GROUP-MEMBER",
        }

    def legacy(self, *, scope="operational_exhaustion", pause="PAUSED_PROVIDER_UNCERTAIN", budget=None, questions=None):
        """An exact old runner publication, independent of today's publication policy."""
        self.state["status"] = pause
        budget = budget or {"category": "operational_recovery", "kind": None, "origin": "unknown", "limit": None}
        receipt = runtime._operational_receipt(
            self.state,
            self.run,
            "hold",
            "Retained provider failure",
            {"status": pause, "stage": "requirements_gather", "budget": budget},
        )
        human.queue(
            self.state,
            scope,
            {"stage": "requirements_gather", "pause_status": pause, "budget": budget},
            request={
                "kind": "blocker",
                "discovered": "Provider exited without a report",
                "impact": "The retained attempt is uncertain",
                "decision_needed": "Provide corrective information",
                "options": ["Provide corrective information", "Leave paused"],
            },
            evidence={"resolver_receipt_id": receipt},
            questions=questions,
            next_stage="requirements_gather",
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

    def test_consumed_legacy_reply_cannot_pass_the_idempotent_fast_path(self):
        public, event, _ = self.consumed()
        before = copy.deepcopy(self.state)
        with self.assertRaises(ValueError):
            human.respond_operational(
                self.state, public["request_id"], public["request_token"], event["action"], event["text"]
            )
        self.assertEqual(before, self.state)

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
            write_json=support.atomic_json,
        )

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
        with patch.object(actions, "STEPS", (*actions.STEPS[:4], forbidden)):
            self.assertEqual(
                2, actions.handle(self.writer(), args, None, self.state, self.run / "state.json", self.run, self.root)
            )
        self.assertEqual([], visited)
        self.assertEqual(before, self.state)

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
        admission.checked(self.state, decided, "failure_retry", {"stage": "builder"})
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
        admission.checked(self.state, decided, "failure_retry", proof)
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

        public_tape = replay_view.project(self.state, run_dir=replay_run)
        self.assertIsNotNone(public_tape)
        self.assertNotIn("recovered_receipt_id", str(public_tape))
        self.assertNotIn("operational_diagnostic", str(public_tape))
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
