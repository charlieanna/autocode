"""The chat surface is a projection of public task history and admitted actions."""

import copy
import json
import shlex
import unittest
from pathlib import Path

import autocode_chat_history as chat_history
import autocode_chat_view as chat
import autocode_requirements_review as requirements_review
import autocode_run_view as run_view
import autocode_util as util
import goal_fixtures


class ChatViewTests(unittest.TestCase):
    def test_idle_unapproved_planning_handoff_offers_feedback_only_when_ready(self):
        state = {
            "status": "RUNNING",
            "phase": "PLANNING",
            "next_stage": "review_plan",
            "goal_contract": {"approval_status": "draft"},
        }
        public = {"status": "RUNNING", "needs": {"kind": "continue"}, "controller": {"kind": "ready"}}
        result = chat.project(state, public, workspace="/ws", run_dir="/run")
        self.assertEqual("feedback", result["input"]["kind"])
        self.assertEqual(["--feedback", "MESSAGE"], shlex.split(result["input"]["next_command"])[-2:])
        for change in ({"controller": {"kind": "running"}}, {"controller": {}}, {"needs": {"kind": "resume"}}):
            with self.subTest(change=change):
                self.assertIsNone(chat.project(state, {**public, **change}, workspace="/ws", run_dir="/run")["input"])

    def test_semantic_stops_offer_only_the_permitted_named_feedback_action(self):
        for status in ("RESOLVER_PENDING", "PAUSED_COMPLETION_GATE"):
            with self.subTest(status=status):
                state = {"status": status, "task": "Fix the total"}
                before = copy.deepcopy(state)
                result = self.project(state, "resume")
                self.assertEqual("feedback", result["input"]["kind"])
                self.assertEqual(
                    [
                        "autocode",
                        "--workspace",
                        "/a project",
                        "--run-dir",
                        "/a project/.autocode/run",
                        "--feedback",
                        "MESSAGE",
                    ],
                    shlex.split(result["input"]["next_command"]),
                )
                self.assertEqual(before, state)

    def test_requested_pause_feedback_requires_a_clean_resume_checkpoint(self):
        for status in ("PAUSED_REQUESTED", "PAUSED_INTERVENTION"):
            state = {"status": status}
            before = copy.deepcopy(state)
            self.assertEqual("feedback", self.project(state, "resume")["input"]["kind"])
            self.assertEqual(before, state)
            self.assertIsNone(self.project(state, "continue")["input"])
            for change in (
                {"active_runner_check": {"kind": "regression_proof"}},
                {"active_stage": {"stage": "tester"}},
                {"uncertain_artifacts": ["attempt"]},
                {"pending_questions": [{"id": "Q1"}]},
                {"resolver_human_proposal": {"scope": "blocker"}},
                {"user_request": {"kind": "human_review"}},
                {"pause_intent": {"held_pause": {"status": "PAUSED_COST_LIMIT"}}},
                {"applied_interventions": [{"kind": "stop"}]},
            ):
                with self.subTest(status=status, change=change):
                    self.assertIsNone(self.project({**state, **change}, "resume")["input"])

    def test_semantic_feedback_does_not_bypass_operational_or_human_gates(self):
        import autocode_goals as goals

        for status in ("RESOLVER_PENDING", "PAUSED_COMPLETION_GATE"):
            for extra in (
                {"user_request": {"kind": "human_review"}},
                {"user_request": {"kind": "blocker"}},
                {
                    goals.RESOLVER_PROPOSAL_KEY: {
                        "scope": "operational_exhaustion",
                        "origin": {"pause_status": "PAUSED_COST_LIMIT"},
                    }
                },
            ):
                with self.subTest(status=status, extra=extra):
                    self.assertIsNone(self.project({"status": status, **extra}, "resume")["input"])
            self.assertIsNone(self.project({"status": status}, "continue")["input"])
            for scope in ("blocker", "operational_exhaustion"):
                public = {"status": status, "needs": {"kind": "resume", "resolver_scope": scope}}
                self.assertIsNone(chat.project({"status": status}, public, workspace="/ws", run_dir="/run")["input"])

    def test_spend_confirmation_is_offered_at_the_bound_prepare_plan_answer(self):
        state = {
            "status": "WAITING_FOR_USER",
            "settings": {"spec_first": True},
            "requirements_handoff": {"report": {"intended_outcome": "Build a greeting"}},
        }
        question = requirements_review.review_question(state)
        public = {"status": state["status"], "needs": {"kind": "answer", "questions": [question]}}
        before = copy.deepcopy((state, public))
        result = chat.project(state, public)
        review = result["review"]
        self.assertEqual(question["id"], review["question_id"])
        self.assertEqual("plan", review["answer"])
        self.assertIn("selected models", review["spend_confirmation"]["detail"])
        self.assertIn("Building still waits", review["spend_confirmation"]["detail"])
        schema = json.loads((Path(__file__).resolve().parents[1] / "docs/schemas/status-view.schema.json").read_text())
        util.validate_schema(result, schema["properties"]["view"]["properties"]["chat"])
        self.assertEqual(before, (state, public))
        public["needs"]["questions"] = [{"id": "clarify", "question": "What should we build?"}]
        self.assertIsNone(chat.project(state, public)["review"])

    def test_new_bugfix_confirmation_describes_planning_and_keeps_legacy_offers(self):
        for standard in (False, True):
            with self.subTest(standard=standard):
                state = {
                    "status": "WAITING_FOR_USER",
                    "settings": {"spec_first": True},
                    "requirements_handoff": {"report": {"job": "bugfix", "intended_outcome": "Fix history"}},
                }
                if standard:
                    state["settings"]["implementation_flow"] = "standard"
                question = requirements_review.review_question(state)
                public = {"status": state["status"], "needs": {"kind": "answer", "questions": [question]}}
                offer = chat.project(state, public)["review"]
                expected = "build" if standard else "bugfix"
                self.assertEqual(chat.APPROVAL_OFFERS[expected][0], offer["label"])
                self.assertEqual(chat.APPROVAL_OFFERS[expected][1], offer["spend_confirmation"]["title"])
                self.assertEqual("plan", offer["answer"])

    def test_connection_stop_explains_the_user_action_without_dashboard_jargon(self):
        state = {"status": "PAUSED_TRANSPORT_CHANGED", "task": "Make it friendlier"}
        result = self.project(state, need="resume")
        self.assertEqual(
            "Your model connection settings changed. Check Connections before continuing.", result["notice"]
        )
        self.assertIsNone(result["input"])
        for status in ("PAUSED_JOB_FAILURE", "PAUSED_PROVIDER_UNCERTAIN"):
            with self.subTest(status=status):
                text = chat.notice({"status": status})
                self.assertTrue(text)
                self.assertNotIn("Paused", text)
                self.assertNotIn("no task list", text)

    def test_a_failed_step_names_no_control_only_one_client_has(self):
        # The terminal chat has no "Run details" button; every client shows the run's details its own way.
        text = chat.notice({"status": "PAUSED_JOB_FAILURE"})
        self.assertEqual("I couldn’t finish this step, and no reason was recorded. Your work so far is saved.", text)
        self.assertNotIn("Run details", text)

    def test_a_stopped_step_says_why_and_the_next_action_instead_of_pointing_at_empty_details(self):
        # A chat said only "The run's details say what needs attention." while the details named nothing
        # to act on: the notice now carries the core's own summary and the action the view offers.
        cause = "builder output rejected ($: missing summary); inspect the output probe"
        state = {"status": "PAUSED_INVALID_OUTPUT", "next_stage": "builder", "stop_reason": cause}
        view = run_view.view(state, workspace=Path("/ws"), run_dir=Path("/run"))
        text = view["chat"]["notice"]
        self.assertEqual(
            "I couldn’t finish this step. The saved report could not be accepted in its current form. "
            "Your work so far is saved; choose “Resume after correction” when you’re ready. "
            "The run’s details show the recorded reason.",
            text,
        )
        self.assertEqual(cause, view["recovery"]["saved_reason"])
        for raw in ("output probe", "missing summary", "--resume-paused", "PAUSED_"):
            self.assertNotIn(raw, text)
        # A plain resume the view offers without a labelled recovery action is still named as the next step.
        failed = run_view.view(
            {"status": "PAUSED_JOB_FAILURE", "next_stage": "builder", "stop_reason": "exit 2"},
            workspace=Path("/ws"),
            run_dir=Path("/run"),
        )
        self.assertIn("The step failed.", failed["chat"]["notice"])
        self.assertIn("continue to try this step again", failed["chat"]["notice"])

    def test_the_testers_disagreement_says_what_it_found_and_asks_how_the_plan_should_change(self):
        # Live run, 2026-11-11: the chat said "Tester stopped before starting another step. Inspect the saved
        # reason" and offered plan feedback, but never said which case disagreed or why.
        import autocode_acceptance_tests_job as job

        state = {"status": "RUNNING", "next_stage": "write_acceptance_tests"}
        job._pause(state, ["AC2 already works: count_chars('') returns 0 today"])
        view = run_view.view(state, workspace=Path("/ws"), run_dir=Path("/run"))
        text = view["chat"]["notice"]
        self.assertEqual(
            "Before building, the Tester found that the approved plan and today’s code disagree: "
            "AC2 already works: count_chars('') returns 0 today. Tell me how the plan should change; "
            "the Tester then runs again on the revised plan.",
            text,
        )
        self.assertEqual("feedback", view["chat"]["input"]["kind"])
        # A finding that names machinery is not repeated; the question still stands.
        flagged = {"status": "RUNNING", "next_stage": "write_acceptance_tests"}
        job._pause(flagged, ["AC2 cannot be tested: it needs `--live`"])
        text = run_view.view(flagged, workspace=Path("/ws"), run_dir=Path("/run"))["chat"]["notice"]
        self.assertTrue(text.startswith("Before building, the Tester found that the approved plan"), text)
        self.assertNotIn("--live", text)

    def test_the_testers_other_stops_say_what_happened_instead_of_pointing_at_a_reason(self):
        reason = "The runner could not establish the approved pre-build proof: guard AG1 does not pass."
        state = {"status": "PAUSED_ACCEPTANCE_TESTS", "next_stage": "write_acceptance_tests", "stop_reason": reason}
        view = run_view.view(state, workspace=Path("/ws"), run_dir=Path("/run"))
        self.assertIn("the Tester's tests did not behave on today's code", view["recovery"]["what_happened"])
        self.assertNotIn("stopped before starting another step", view["chat"]["notice"])

    def test_a_stop_with_no_recorded_reason_says_so_and_offers_to_continue(self):
        view = run_view.view(
            {"status": "PAUSED_FUTURE_REASON", "next_stage": "builder"}, workspace=Path("/ws"), run_dir=Path("/run")
        )
        self.assertIn("It recorded no reason.", view["recovery"]["what_happened"])
        self.assertNotIn("Inspect the saved reason", view["recovery"]["what_happened"])
        text = view["chat"]["notice"]
        self.assertIn("It recorded no reason.", text)
        self.assertIn("continue to try this step again", text)
        self.assertNotIn("details show the recorded reason", text)
        # With a saved reason the same stop points at it.
        saved = run_view.view(
            {"status": "PAUSED_FUTURE_REASON", "next_stage": "builder", "stop_reason": "x"},
            workspace=Path("/ws"),
            run_dir=Path("/run"),
        )
        self.assertIn("Inspect the saved reason", saved["recovery"]["what_happened"])
        self.assertIn("details show the recorded reason", saved["chat"]["notice"])

    def test_a_run_that_cannot_continue_says_why_and_to_start_a_new_task(self):
        retired = (
            "This run was saved before AutoCode named its stages after their jobs: its routes terra (now builder) "
            "use the old names, which this runtime no longer runs. Its status and saved reports stay readable; "
            "start a new task to continue the work."
        )
        need = {"kind": "resume", "reason": retired, "recovery_hint": retired, "new_run_required": True}
        text = chat.notice({"status": "PAUSED_STALE_HANDOFF", "needs": need, "recovery": {"saved_reason": "x"}})
        self.assertEqual("I can’t continue this run. " + retired, text)
        # A hint that names a flag or omits the next step is replaced by the plain next step.
        flagged = dict(need, reason="Use --edit-goal FILE", recovery_hint="Use --edit-goal FILE")
        text = chat.notice({"status": "PAUSED_COMPONENT_PLAN", "needs": flagged})
        self.assertEqual(
            "I can’t continue this run. Start a new task to carry on; this chat’s saved work stays readable.", text
        )

    def test_a_pause_someone_asked_for_is_not_reported_as_a_failure(self):
        for status in ("PAUSED_INTERVENTION", "PAUSED_REQUESTED"):
            with self.subTest(status=status):
                text = chat.notice({"status": status})
                self.assertIn("Paused as you asked", text)
                self.assertNotIn("couldn’t finish", text)
        self.assertIn("couldn’t finish", chat.notice({"status": "PAUSED_JOB_FAILURE"}))
        applying = chat.notice({"status": "PAUSED_INTERVENTION_PENDING"})
        self.assertIn("Paused to apply your request", applying)
        self.assertNotIn("couldn’t finish", applying)

    def test_applied_feedback_does_not_claim_the_person_requested_a_pause(self):
        for stage in ("Requirements discovery", "Planner revision"):
            public = {
                "status": "PAUSED_INTERVENTION",
                "needs": {"kind": "resume", "reason": f"Queued feedback was applied; explicitly continue to {stage}."},
            }
            before = copy.deepcopy(public)
            self.assertEqual("Feedback applied. Your work is saved; continue to update the plan.", chat.notice(public))
            self.assertEqual(before, public)
        # A real pause request and a different pending action retain their own explanations.
        for status, reason in (
            ("PAUSED_INTERVENTION", "Queued pause was applied; explicitly resume when ready."),
            ("PAUSED_REQUESTED", "Queued feedback was applied; explicitly continue to Planner revision."),
        ):
            self.assertIn("Paused as you asked", chat.notice({"status": status, "needs": {"reason": reason}}))

    def test_a_run_autocode_ended_itself_says_why_and_asks_nothing(self):
        # #2485: AutoCode's own end is not "paused as you asked", and names no flag, token or packet.
        ended = {"status": "PAUSED_INTERVENTION", "recovery": {"outcome": {"reason": "the sol check kept failing: x."}}}
        text = chat.notice(ended)
        self.assertEqual(
            "I couldn’t finish this task: the sol check kept failing: x. Your work so far is saved; new work is a new task.",
            text,
        )
        self.assertIn(
            "Paused as you asked", chat.notice({"status": "PAUSED_INTERVENTION", "recovery": {"outcome": None}})
        )

    def test_running_notice_gates_on_live_supervision_only(self):
        self.assertEqual(
            "Working on your request…", chat.notice({"status": "RUNNING", "liveness": {"kind": "supervised"}})
        )
        self.assertEqual(
            "Working on your request…", chat.notice({"status": "RUNNING", "liveness": {"kind": "unsupervised"}})
        )
        self.assertIsNone(chat.notice({"status": "RUNNING", "liveness": {"kind": "unknown"}}))
        self.assertIsNone(chat.notice({"status": "RUNNING", "liveness": {"kind": "stopped"}}))
        self.assertIsNone(chat.notice({"status": "RUNNING"}))
        state = {"status": "RUNNING", "task": "Build it"}
        result = self.project(state, need="continue")
        self.assertIsNone(result["notice"])

    def project(self, state, need="approve_plan", done=False):
        return chat.project(
            state,
            {"status": state["status"], "done": done, "needs": {"kind": need}},
            workspace="/a project",
            run_dir="/a project/.autocode/run",
        )

    def test_plan_feedback_is_named_without_approval_or_provider_flags(self):
        state = {"status": "AWAITING_GOAL_APPROVAL", "task": "Build it", "user_events": []}
        before = copy.deepcopy(state)
        result = self.project(state)
        self.assertEqual("feedback", result["input"]["kind"])
        self.assertEqual(
            ["autocode", "--workspace", "/a project", "--run-dir", "/a project/.autocode/run", "--feedback", "MESSAGE"],
            shlex.split(result["input"]["next_command"]),
        )
        self.assertEqual(before, state)
        self.assertEqual(result, self.project(state))

    def test_completed_run_names_follow_up_only_for_real_completion(self):
        state = {"status": "TASK_COMPLETE", "task": "Build it"}
        self.assertEqual("follow_up", self.project(state, "done", True)["input"]["kind"])
        self.assertIsNone(self.project(state, "done", False)["input"])
        state["status"] = "RUNNING"
        self.assertIsNone(self.project(state, "continue")["input"])

    def test_invalid_planning_reports_offer_correction_without_retry_or_approval(self):
        import autocode_roles as roles

        for stage in (*roles.ALL_PLANNING_STAGES, "write_acceptance_tests"):
            with self.subTest(stage=stage):
                state = {"status": "PAUSED_INVALID_OUTPUT", "next_stage": stage, "task": "Fix it"}
                before = copy.deepcopy(state)
                result = self.project(state, "resume")
                self.assertEqual("feedback", result["input"]["kind"])
                self.assertEqual(
                    [
                        "autocode",
                        "--workspace",
                        "/a project",
                        "--run-dir",
                        "/a project/.autocode/run",
                        "--feedback",
                        "MESSAGE",
                    ],
                    shlex.split(result["input"]["next_command"]),
                )
                self.assertEqual(before, state)

    def test_invalid_execution_reports_and_person_only_holds_offer_no_correction(self):
        for stage in (None, "unknown", "builder", "tester", "review_completion", "investigate_bug"):
            with self.subTest(stage=stage):
                state = {"status": "PAUSED_INVALID_OUTPUT", "next_stage": stage}
                self.assertIsNone(self.project(state, "resume")["input"])
        for stage in ("finalize_plan", "write_acceptance_tests"):
            for request in ({"kind": "human_review"}, {"kind": "blocker"}):
                state = {"status": "PAUSED_INVALID_OUTPUT", "next_stage": stage, "user_request": request}
                self.assertIsNone(self.project(state, "resume")["input"])

    def test_invalid_planning_report_does_not_release_held_operational_authority(self):
        import autocode_goals as goals

        state = {
            "status": "PAUSED_INVALID_OUTPUT",
            "next_stage": "finalize_plan",
            goals.RESOLVER_PROPOSAL_KEY: {
                "scope": "operational_exhaustion",
                "origin": {"pause_status": "PAUSED_TIME_LIMIT"},
            },
        }
        self.assertIsNone(self.project(state, "resume")["input"])
        state["next_stage"] = "write_acceptance_tests"
        self.assertIsNone(self.project(state, "resume")["input"])

    def test_a_retained_invalid_report_does_not_offer_feedback_while_running(self):
        state = {"status": "PAUSED_INVALID_OUTPUT", "next_stage": "finalize_plan"}
        public = {"status": "RUNNING", "needs": {"kind": "continue"}}
        self.assertIsNone(chat.project(state, public, workspace="/ws", run_dir="/run")["input"])

    def test_operational_pause_and_human_review_cannot_be_bypassed_with_feedback(self):
        for request in (
            {"kind": "human_review"},
            {"kind": "blocker", "origin": {"pause_status": "PAUSED_PROVIDER_UNCERTAIN"}},
        ):
            state = {"status": "WAITING_FOR_USER", "user_request": request}
            public = {
                "status": state["status"],
                "needs": {"kind": "answer", "resolver_scope": "operational_exhaustion"},
            }
            self.assertIsNone(chat.project(state, public, workspace="/ws", run_dir="/run")["input"])

    def test_the_testers_pause_offers_plan_feedback_unless_it_is_operational(self):
        state = {"status": "PAUSED_ACCEPTANCE_TESTS", "task": "Fix it"}
        public = {"status": state["status"], "needs": {"kind": "resume"}}
        result = chat.project(state, public, workspace="/ws", run_dir="/run")
        self.assertEqual("feedback", result["input"]["kind"])
        self.assertEqual(
            ["autocode", "--workspace", "/ws", "--run-dir", "/run", "--feedback", "MESSAGE"],
            shlex.split(result["input"]["next_command"]),
        )
        held = {**state, "status": "WAITING_FOR_USER", "user_request": {"kind": "blocker"}}
        public = {"status": held["status"], "needs": {"kind": "answer", "resolver_scope": "operational_exhaustion"}}
        self.assertIsNone(chat.project(held, public, workspace="/ws", run_dir="/run")["input"])
        other = {"status": "PAUSED_REPEATED_FAILURE"}
        public = {"status": other["status"], "needs": {"kind": "resume"}}
        self.assertIsNone(chat.project(other, public, workspace="/ws", run_dir="/run")["input"])

    def test_pending_operational_request_offers_bound_reply_only(self):
        state = {"status": "WAITING_FOR_USER", "user_request": {"kind": "blocker"}}
        need = {
            "kind": "answer",
            "resolver_scope": "blocker",
            "resolver_request_id": "req-1",
            "resolver_token": "current-token",
            "questions": [
                {"id": "q1", "question": "What changed?", "why": "Check the cause", "options": ["Leave blocked"]}
            ],
        }
        public = {"status": state["status"], "needs": need}
        result = chat.project(state, public, workspace="/ws", run_dir="/run")
        self.assertEqual("resolver_response", result["input"]["kind"])
        schema = json.loads((Path(__file__).resolve().parents[1] / "docs/schemas/status-view.schema.json").read_text())
        util.validate_schema(result, schema["properties"]["view"]["properties"]["chat"])
        self.assertIn("What changed?", result["messages"][-1]["text"])
        self.assertIn("Leave blocked", result["messages"][-1]["text"])
        self.assertEqual(
            [
                "autocode",
                "--workspace",
                "/ws",
                "--run-dir",
                "/run",
                "--resolver-request",
                "req-1",
                "--resolver-token",
                "-",
                "--resolver-response",
                "provide_information",
                "--resolver-message",
                "MESSAGE",
            ],
            shlex.split(result["input"]["next_command"]),
        )
        # #2958: the named command reads this run's own request, so the relayed text carries no value.
        self.assertNotIn("current-token", result["input"]["next_command"])
        self.assertNotIn("AUTOCODE_RESOLVER_TOKEN", result["input"]["next_command"])
        for missing in ("resolver_request_id", "resolver_token"):
            invalid = {**public, "needs": {k: v for k, v in need.items() if k != missing}}
            self.assertIsNone(chat.project(state, invalid, workspace="/ws", run_dir="/run")["input"])
        for change in ({"route": {"role": "tester"}}, {"resolver_scope": "requirements"}):
            invalid = {**public, "needs": {**need, **change}}
            self.assertIsNone(chat.project(state, invalid, workspace="/ws", run_dir="/run")["input"])
        human = {**state, "user_request": {"kind": "human_review"}}
        self.assertIsNone(chat.project(human, public, workspace="/ws", run_dir="/run")["input"])

    def test_saved_operational_reply_remains_visible_after_request_is_consumed(self):
        state = {
            "status": "PAUSED_COMPLETION_GATE",
            "task": "Build it",
            "resolver": {
                "human_responses": [{"request_id": "r1", "text": "Leave this task blocked", "actor": "user_cli"}],
                "human_escalations": {
                    "r1": {
                        "identity": {"proposal": {"request": {"questions": [{"question": "How should we proceed?"}]}}}
                    }
                },
            },
        }
        rows = self.project(state, "resume")["messages"]
        self.assertEqual(["Build it", "How should we proceed?", "Leave this task blocked"], [r["text"] for r in rows])

    def test_operational_recovery_uses_human_summary_with_unchanged_authority(self):
        for scope in ("blocker", "operational_exhaustion"):
            with self.subTest(scope=scope):
                state = {
                    "status": "WAITING_FOR_USER",
                    "settings": {"spec_first": True},
                    "next_stage": "requirements_gather",
                    "user_request": {"kind": "blocker", "discovered": "Provider rejected authentication (HTTP 401)."},
                }
                public = {
                    "status": state["status"],
                    "recovery": {
                        "what_happened": "The model connection could not authenticate.",
                        "saved_reason": state["user_request"]["discovered"],
                    },
                    "needs": {
                        "kind": "answer",
                        "resolver_scope": scope,
                        "resolver_request_id": "req-1",
                        "resolver_token": "current-token",
                        "questions": [
                            {
                                "id": "q1",
                                "question": "What changed? Retry with --abandon-stage 001/requirements-01.",
                                "options": ["Provide information", "Leave paused"],
                            }
                        ],
                    },
                }
                before = copy.deepcopy((state, public))
                result = chat.project(state, public, workspace="/ws", run_dir="/run")
                self.assertEqual(["resolver-req-1"], [row["id"] for row in result["messages"]])
                text = result["messages"][0]["text"]
                if scope == "operational_exhaustion":
                    self.assertIn(public["recovery"]["what_happened"], text)
                    self.assertNotIn("HTTP 401", text)
                    self.assertIn("details say what needs attention", text)
                    self.assertIn("does not authorize a retry", text)
                    self.assertNotIn("--abandon-stage", text)
                else:
                    self.assertIn("What changed?", text)
                self.assertEqual("resolver_response", result["input"]["kind"])
                self.assertNotIn("AUTOCODE_RESOLVER_TOKEN=current-token", result["input"]["next_command"])
                self.assertNotIn("current-token", result["input"]["next_command"])
                # No projected field carries the value: relaying or logging this view text is safe.
                self.assertNotIn("current-token", json.dumps(result))
                self.assertIn("--resolver-token -", result["input"]["next_command"])
                self.assertIn("--resolver-response provide_information", result["input"]["next_command"])
                self.assertEqual(before, (state, public))

    def test_operational_failure_without_recovery_summary_does_not_leak_diagnostics(self):
        cause = (
            "Completed requirements_gather_report_repair output was rejected ($: missing assistant_reply); "
            "inspect the saved output probe. Cross-model verification violated."
        )
        state = {"status": "WAITING_FOR_USER", "user_request": {"kind": "blocker", "discovered": cause}}
        public = {
            "status": state["status"],
            "needs": {
                "kind": "answer",
                "resolver_scope": "operational_exhaustion",
                "resolver_request_id": "req-1",
                "resolver_token": "current-token",
                "questions": [{"question": cause}],
            },
        }
        for recovery in (None, {}, {"what_happened": ""}):
            with self.subTest(recovery=recovery):
                public["recovery"] = recovery
                before = copy.deepcopy((state, public))
                result = chat.project(state, public, workspace="/ws", run_dir="/run")
                text = result["messages"][0]["text"]
                self.assertIn("couldn’t finish", text)
                self.assertIn("details say what needs attention", text)
                self.assertIn("Reply with what changed", text)
                self.assertIn("does not authorize a retry", text)
                for raw in ("requirements_gather", "assistant_reply", "output probe", "Cross-model"):
                    self.assertNotIn(raw, text)
                self.assertEqual("resolver_response", result["input"]["kind"])
                self.assertEqual(before, (state, public))

    def test_public_view_keeps_raw_failure_in_details_and_human_summary_in_chat(self):
        cause = "requirements_gather_report_repair: $: missing assistant_reply; inspect the output probe"
        state = {
            "status": "WAITING_FOR_USER",
            "next_stage": "requirements_gather",
            "user_request": {"kind": "blocker", "discovered": cause},
            "stop_reason": cause,
            "pending_questions": [{"id": "q1", "question": cause, "options": []}],
            "resolver_human_request": {
                "request_id": "req-1",
                "request_token": "current-token",
                "scope": "operational_exhaustion",
            },
            "resolver": {
                "human_escalations": {
                    "req-1": {
                        "status": "pending",
                        "identity": {"proposal": {"origin": {"pause_status": "PAUSED_REPEATED_FAILURE"}}},
                    }
                }
            },
        }
        before = copy.deepcopy(state)
        view = run_view.view(state, workspace=Path("/ws"), run_dir=Path("/run"))
        text = view["chat"]["messages"][0]["text"]
        self.assertIn("The same step repeatedly failed.", text)
        self.assertIn("details say what needs attention", text)
        for raw in ("requirements_gather", "assistant_reply", "output probe"):
            self.assertNotIn(raw, text)
        self.assertEqual(cause, view["recovery"]["saved_reason"])
        self.assertEqual(cause, view["needs"]["questions"][0]["question"])
        self.assertEqual("resolver_response", view["chat"]["input"]["kind"])
        self.assertEqual(before, state)

    def test_routed_and_unbound_operational_questions_stay_visible_in_requirements(self):
        state = {
            "status": "WAITING_FOR_USER",
            "settings": {"spec_first": True},
            "next_stage": "requirements_gather",
            "user_request": {"kind": "blocker"},
        }
        question = "Which model should Requirements use after its provider quota stop?"
        need = {
            "kind": "answer",
            "resolver_scope": "operational_exhaustion",
            "resolver_request_id": "req-1",
            "resolver_token": "current-token",
            "questions": [{"id": "route-planner", "question": question, "options": []}],
        }
        routed = {
            **need,
            "route": {
                "question_id": "route-planner",
                "role": "planner",
                "cause": "quota",
                "current_model": "p/old",
                "stopped_model": "p/old",
                "candidates": ["p/new"],
            },
        }
        for open_need in (
            routed,
            {key: value for key, value in need.items() if key != "resolver_request_id"},
            {key: value for key, value in need.items() if key != "resolver_token"},
        ):
            with self.subTest(need=open_need):
                public = {"status": state["status"], "needs": open_need}
                result = chat.project(state, public, workspace="/ws", run_dir="/run")
                [message] = result["messages"]
                self.assertTrue(message["id"].startswith("requirements-question-"))
                self.assertEqual(question, result["messages"][0]["text"])
                self.assertIsNone(result["input"])

    def test_each_requirements_question_is_its_own_message(self):
        # A client shows a message id once, so a shared id hid every question after the first (#1792).
        def ids(question_id, text):
            need = {"kind": "answer", "questions": [{"id": question_id, "question": text, "options": []}]}
            state = {
                "status": "WAITING_FOR_USER",
                "settings": {"spec_first": True},
                "next_stage": "requirements_gather",
            }
            result = chat.project(state, {"status": "WAITING_FOR_USER", "needs": need}, workspace="/ws", run_dir="/run")
            return [row["id"] for row in result["messages"] if row["id"].startswith(chat.REQUIREMENTS_QUESTION)]

        first, again = ids("Q1", "Who is it for?"), ids("Q1", "Who is it for?")
        second = ids("Q2", "What should happen with no name?")
        self.assertEqual(first, again, "the same question keeps its id across reads")
        self.assertEqual(1, len(first))
        self.assertNotEqual(first, second)

    def test_automatic_repository_routing_is_not_presented_as_a_requirements_question(self):
        for coordinated in ("anchor", "member", "manual"):
            with self.subTest(coordinated=coordinated):
                state = {
                    "status": "WAITING_FOR_USER",
                    "settings": {"spec_first": True, "coordination_member": coordinated == "member"},
                    "next_stage": "draft_plan",
                    "requirements_handoff": {
                        "report": {
                            "assistant_reply": "I understand the history should identify the operation.",
                            "workspace_scope": {
                                "spans_repositories": True,
                                "external_parts": [
                                    {
                                        "target": "history",
                                        "work": "Preserve the operation",
                                        "source_quote": "Fix history",
                                    }
                                ],
                            },
                        }
                    },
                }
                scope = requirements_review.scope_question.question(state)
                self.assertIsNotNone(scope)
                public = {"status": state["status"], "needs": {"kind": "answer", "questions": [scope]}}
                if coordinated == "anchor":
                    public["coordination"] = {"version": 1, "status_command": ["named-coordinator-status"]}
                before = copy.deepcopy((state, public))
                result = chat.project(state, public, workspace="/ws", run_dir="/run")
                asked = [row for row in result["messages"] if row["id"].startswith(chat.REQUIREMENTS_QUESTION)]
                self.assertEqual([scope["question"]] if coordinated == "manual" else [], [row["text"] for row in asked])
                self.assertIn(
                    state["requirements_handoff"]["report"]["assistant_reply"],
                    [row["text"] for row in result["messages"]],
                )
                self.assertEqual(before, (state, public), "rendering must not answer or remove the scope gate")
                genuine = {"id": "Q1", "question": "Which operation is missing?", "options": []}
                public["needs"]["questions"] = [genuine]
                result = chat.project(state, public, workspace="/ws", run_dir="/run")
                self.assertIn(genuine["question"], [row["text"] for row in result["messages"]])

    def test_a_report_repair_note_never_displaces_the_question_the_person_must_answer(self):
        # The Requirements stage's own prose is not always a question: after a report repair
        # it describes the repair, and a client asks whatever this message carries.
        note = "Repaired the rejected requirements report by adding the missing acceptance_tests field."
        question = "Should non-ASCII letters count as alphanumeric?"
        state = {
            "status": "WAITING_FOR_USER",
            "settings": {"spec_first": True},
            "next_stage": "requirements_gather",
            "requirements_handoff": {"report": {"assistant_reply": note}},
        }
        need = {"kind": "answer", "questions": [{"id": "Q1", "question": question, "options": []}]}
        result = chat.project(state, {"status": "WAITING_FOR_USER", "needs": need}, workspace="/ws", run_dir="/run")
        asked = [row for row in result["messages"] if row["id"].startswith(chat.REQUIREMENTS_QUESTION)]
        self.assertEqual([question], [row["text"] for row in asked])
        # The stage's own words are kept beside the question, never in place of it.
        said = [row for row in result["messages"] if row["id"].startswith(chat.REQUIREMENTS_SAY)]
        self.assertEqual([note], [row["text"] for row in said])

    def test_the_stage_prose_is_not_repeated_when_it_already_is_the_question(self):
        question = "Should non-ASCII letters count as alphanumeric?"
        state = {
            "status": "WAITING_FOR_USER",
            "settings": {"spec_first": True},
            "next_stage": "requirements_gather",
            "requirements_handoff": {"report": {"assistant_reply": question}},
        }
        need = {"kind": "answer", "questions": [{"id": "Q1", "question": question, "options": []}]}
        result = chat.project(state, {"status": "WAITING_FOR_USER", "needs": need}, workspace="/ws", run_dir="/run")
        self.assertEqual([question], [row["text"] for row in result["messages"]])

    def test_the_stage_prose_and_its_question_worded_alike_are_one_message(self):
        """A Jira link the stage could not open: the chat showed "I couldn't access DTI-12447. Could you paste
        the ticket description ...?" and, right under it, "Could you paste the description of DTI-12447 ...?"."""
        spoken = (
            "I couldn\u2019t access DTI-12447. Could you paste the ticket description, including what is "
            "going wrong and what should happen instead?"
        )
        question = (
            "Could you paste the description of DTI-12447, including what is going wrong and what should "
            "happen instead?"
        )
        state = {
            "status": "WAITING_FOR_USER",
            "settings": {"spec_first": True},
            "next_stage": "requirements_gather",
            "requirements_handoff": {"report": {"assistant_reply": spoken}},
        }
        need = {"kind": "answer", "questions": [{"id": "Q1", "question": question, "options": []}]}
        result = chat.project(state, {"status": "WAITING_FOR_USER", "needs": need}, workspace="/ws", run_dir="/run")
        self.assertEqual([spoken], [row["text"] for row in result["messages"]])
        self.assertTrue(result["messages"][0]["id"].startswith(chat.REQUIREMENTS_QUESTION))

    def test_a_question_copied_without_its_code_marks_is_one_message(self):
        """A live Claude run on the same ticket link (2026-11-10) copied its question word for word but
        wrote `pages.py` in the reply and pages.py in the question, and its reply ends in a full stop."""
        spoken = (
            "I can't open the Jira ticket DTI-12447 from here, so I don't know what the bug is. What is the "
            "wrong behavior, and what should happen instead? A short description, the ticket's text, or "
            "reproduction steps would help. If it's in `pages.py`, tell me which page or function."
        )
        question = (
            "What is the wrong behavior, and what should happen instead? A short description, the ticket's "
            "text, or reproduction steps would help. If it's in pages.py, tell me which page or function."
        )
        state = {
            "status": "WAITING_FOR_USER",
            "settings": {"spec_first": True},
            "next_stage": "requirements_gather",
            "requirements_handoff": {"report": {"assistant_reply": spoken}},
        }
        need = {"kind": "answer", "questions": [{"id": "Q1", "question": question, "options": []}]}
        result = chat.project(state, {"status": "WAITING_FOR_USER", "needs": need}, workspace="/ws", run_dir="/run")
        self.assertEqual([spoken], [row["text"] for row in result["messages"]])
        self.assertTrue(result["messages"][0]["id"].startswith(chat.REQUIREMENTS_QUESTION))

    def test_prose_that_is_not_the_same_ask_keeps_the_question_beside_it(self):
        question = "Should the export include archived projects, or only the active ones in this workspace?"
        for spoken in (
            "Which format do you want for the export?",  # another question
            "I will list the archived projects and the active ones in the export, then ask you to confirm.",  # no ask
            "Archived?",  # too short to be the question
        ):
            with self.subTest(spoken=spoken):
                self.assertFalse(chat.rewords(spoken, question))
        self.assertFalse(chat.rewords("Fine, which one?", "Which one?"), "a short question is never matched by words")
        self.assertTrue(chat.rewords(f"Thanks. {question}", question))

    def test_a_greeting_with_the_complete_question_is_one_question_message(self):
        question = "Should I investigate the history entry or fix the display?"
        spoken = f"Hi! {question}"
        state = {
            "status": "WAITING_FOR_USER",
            "settings": {"spec_first": True},
            "next_stage": "requirements_gather",
            "requirements_handoff": {"report": {"assistant_reply": spoken}},
        }
        need = {"kind": "answer", "questions": [{"id": "Q1", "question": question, "options": []}]}
        result = chat.project(state, {"status": "WAITING_FOR_USER", "needs": need}, workspace="/ws", run_dir="/run")
        self.assertEqual([spoken], [row["text"] for row in result["messages"]])
        self.assertTrue(result["messages"][0]["id"].startswith(chat.REQUIREMENTS_QUESTION))
        self.assertEqual("feedback", result["input"]["kind"])

    def test_history_preserves_original_request_answers_feedback_and_followups(self):
        state = {
            "status": "TASK_COMPLETE",
            "task": "rewritten request with context",
            "turns": [{"previous": {"task": "Original request"}}],
            "user_events": [
                {"kind": "answer", "question": {"question": "Which language?"}, "text": "Python"},
                {"kind": "brief_feedback", "id": "f1", "text": "Keep dependencies out"},
                {"kind": "brief_feedback", "id": "f2", "text": "Add a follow-up"},
            ],
        }
        result = self.project(state, "done", True)
        self.assertEqual(
            [
                "Original request",
                "Which language?",
                "Python",
                "Keep dependencies out",
                "Add a follow-up",
                "The requested task is complete.",
            ],
            [row["text"] for row in result["messages"]],
        )
        self.assertEqual(result["messages"], self.project(copy.deepcopy(state), "done", True)["messages"])
        state["user_events"].append({"kind": "brief_feedback", "text": "New request"})
        self.assertNotEqual(result["context"], self.project(state, "done", True)["context"])

    def test_button_answers_read_as_what_the_person_did(self):
        state = {
            "status": "TASK_COMPLETE",
            "turns": [{"previous": {"task": "Build greet.py"}}],
            "user_events": [
                {
                    "kind": "answer",
                    "question_id": "requirements-review-abc",
                    "question": {"question": "Does this match?"},
                    "text": "plan",
                },
                {"kind": "goal_approval", "token": "r2:abc"},
                {"kind": "answer", "question_id": "q-name", "question": {"question": "Name?"}, "text": "plan"},
            ],
        }
        texts = [row["text"] for row in self.project(state, "done", True)["messages"]]
        self.assertEqual(
            [
                "Build greet.py",
                "Does this match?",
                "Looks right. Prepare the plan.",
                "Approved the plan.",
                "Name?",
                "plan",
                "The requested task is complete.",
            ],
            texts,
        )

    def approved_plan(self, revision=1, outcome="Deliver the first plan"):
        body = goal_fixtures.body(human=True)
        body["intended_outcome"] = outcome
        body["acceptance_criteria"][0]["criterion"] = outcome + " works"
        body["git_delivery"] = {"kind": "atomic_commit", "message": "Deliver the approved greeting"}
        contract = {"task_id": "task-1", "revision": revision, "body": body}
        contract["hash"] = util.digest(contract)
        event = {"kind": "goal_approval", "actor": "user_cli", "token": f"r{revision}:{contract['hash']}"}
        contract.update(approval_status="approved", approval_event=event, origin="fixture")
        return contract, event

    def test_approved_plan_stays_before_approval_during_build_and_after_completion(self):
        contract, event = self.approved_plan()
        state = {
            "status": "RUNNING",
            "task": "Build a greeting",
            "goal_contract": contract,
            "user_events": [event],
        }
        before = copy.deepcopy(state)
        view = run_view.view(state, workspace=Path("/ws"), run_dir=Path("/run"))
        rows = view["chat"]["messages"]
        self.assertEqual(["user", "assistant", "user"], [r["role"] for r in rows])
        self.assertEqual("Approved the plan.", rows[-1]["text"])
        text = rows[-2]["text"]
        for expected in (
            "revision 1",
            contract["body"]["intended_outcome"],
            contract["body"]["acceptance_criteria"][0]["criterion"],
            contract["body"]["acceptance_criteria"][0]["verification_method"],
            "your review",
            contract["body"]["constraints"][0],
            contract["body"]["permission_boundaries"][0],
            contract["body"]["git_delivery"]["message"],
        ):
            self.assertIn(expected, text)
        self.assertEqual(before, state)
        state["status"] = "TASK_COMPLETE"
        completed = self.project(state, "done", True)["messages"]
        self.assertEqual(rows, completed[:-1])
        self.assertEqual(completed, self.project(copy.deepcopy(state), "done", True)["messages"])

    def test_each_approval_keeps_its_own_plan_across_revisions_and_followups(self):
        first, first_event = self.approved_plan(1, "First approved outcome")
        second, second_event = self.approved_plan(2, "Second approved outcome")
        draft, _ = self.approved_plan(3, "Unapproved replacement")
        draft.update(approval_status="draft", approval_event=None)
        state = {
            "status": "AWAITING_GOAL_APPROVAL",
            "task": "Follow-up task",
            "turns": [{"event_id": "follow-up", "previous": {"task": "Original task"}}],
            "contract_history": [first, second],
            "goal_contract": draft,
            "user_events": [
                first_event,
                {"kind": "brief_feedback", "id": "follow-up", "text": "Add a feature"},
                second_event,
            ],
        }
        rows = self.project(state)["messages"]
        self.assertEqual(["user", "assistant", "user", "user", "assistant", "user"], [r["role"] for r in rows])
        self.assertIn("First approved outcome", rows[1]["text"])
        self.assertNotIn("Second approved outcome", rows[1]["text"])
        self.assertIn("Second approved outcome", rows[4]["text"])
        self.assertNotIn("Unapproved replacement", "\n".join(r["text"] for r in rows))
        self.assertNotEqual(rows[1]["id"], rows[4]["id"])

    def test_unbound_or_damaged_contract_never_supplies_approved_plan_text(self):
        contract, event = self.approved_plan()
        tampered = copy.deepcopy(contract)
        tampered["body"]["intended_outcome"] = "Changed without approval"
        forged = copy.deepcopy(contract)
        forged["approval_event"] = {**event, "actor": "model"}
        for saved in (None, {}, tampered, forged, {**contract, "approval_event": None}):
            with self.subTest(contract=saved):
                state = {"status": "RUNNING", "goal_contract": saved, "user_events": [event]}
                rows = self.project(state, "continue")["messages"]
                self.assertEqual(["Approved the plan."], [r["text"] for r in rows])

    def test_saved_discussion_answer_and_previous_response_are_visible(self):
        state = {
            "status": "TASK_COMPLETE",
            "task": "Explain it",
            "workflow": {"kind": "discuss"},
            "answer": {"answer": "It uses a queue.", "evidence": [{"claim": "FIFO ordering", "source": "queue.py:3"}]},
        }
        self.assertIn("It uses a queue.", self.project(state, "done", True)["messages"][-1]["text"])
        state.update(
            status="RUNNING",
            turns=[{"event_id": "f1", "previous": {"task": "Explain it", "chat_response": "It uses a queue."}}],
            user_events=[{"kind": "brief_feedback", "id": "f1", "text": "Add a test"}],
        )
        rows = self.project(state, "continue")["messages"]
        self.assertEqual(["Explain it", "It uses a queue.", "Add a test"], [row["text"] for row in rows])

    def test_a_completed_task_worktree_run_says_where_its_work_is(self):
        """The completion message names the branch and the merge and clean-up commands (#2344)."""
        state = {
            "status": "TASK_COMPLETE",
            "task": "Add a goodbye script",
            "task_branch": "autocode/add-a-goodbye-script-4e8c",
            "project_workspace": "/a project",
            "workspace": "/a project/.autocode/worktrees/add-a-goodbye-script-4e8c",
            "final_decision": {"report": {"summary": "The script prints goodbye."}},
        }
        text = self.project(state, "done", True)["messages"][-1]["text"]
        self.assertTrue(
            text.startswith("The script prints goodbye.\n\nDelivered on branch autocode/add-a-goodbye"), text
        )
        self.assertIn(
            "autocode merge --workspace '/a project' '/a project/.autocode/worktrees/add-a-goodbye-script-4e8c'", text
        )
        self.assertIn("autocode clean-worktrees --workspace '/a project' --yes", text)
        in_place = {"status": "TASK_COMPLETE", "task": "Add it", "workspace": "/a project"}
        self.assertEqual(
            "The requested task is complete.", self.project(in_place, "done", True)["messages"][-1]["text"]
        )

    def test_a_hi_in_a_folder_outside_git_hears_nothing_about_folders(self):
        """Typing "hi" in a folder outside Git was answered with a paragraph about a private project and
        its path: nothing is written until a plan is approved, so nothing about folders is said before."""
        for project in ("/tmp/notes/.autocode/projects/0123456789ab", "/tmp/notes"):
            with self.subTest(project=project):
                state = {
                    "status": "WAITING_FOR_USER",
                    "task": "hi",
                    "workspace_bootstrap": {"selected": "/tmp/notes", "project": project},
                    "user_events": [{"kind": "answer", "id": "a1", "text": "Nothing yet, just saying hello."}],
                }
                rows = self.project(state, "continue")["messages"]
                self.assertNotIn(chat_history.BOOTSTRAP_NOTICE, [row["id"] for row in rows])
                self.assertNotIn("Git", "\n".join(row["text"] for row in rows))

    def test_a_private_project_is_explained_once_after_the_plan_is_approved(self):
        state = {
            "status": "RUNNING",
            "task": "Build wordcount.py",
            "workspace_bootstrap": {"selected": "/tmp/notes", "project": "/tmp/notes/.autocode/projects/0123456789ab"},
            "user_events": [{"kind": "goal_approval", "token": "t1"}],
        }
        rows = self.project(state, "continue")["messages"]
        ids = [row["id"] for row in rows]
        self.assertEqual(ids.index("approval-0") + 1, ids.index(chat_history.BOOTSTRAP_NOTICE))
        notice = rows[ids.index(chat_history.BOOTSTRAP_NOTICE)]["text"]
        self.assertEqual(
            "/tmp/notes is not in Git, so I build in a private project of my own and leave your files as they "
            "are. When the build is done, I put it in a new folder in /tmp/notes.",
            notice,
        )
        self.assertNotIn("0123456789ab", notice, "the private project's id is machinery")
        # One disclosure for the whole run: a second approval and a later turn do not repeat it.
        state["user_events"] += [
            {"kind": "goal_approval", "token": "t2"},
            {"kind": "brief_feedback", "id": "f1", "text": "Add a test"},
        ]
        later = self.project(state, "continue")["messages"]
        self.assertEqual(1, sum(row["id"] == chat_history.BOOTSTRAP_NOTICE for row in later))
        self.assertIn("Add a test", [row["text"] for row in later][-1])

    def test_a_legacy_project_is_named_after_approval_and_a_repository_says_nothing(self):
        state = {
            "status": "RUNNING",
            "task": "Build wordcount.py",
            "workspace_bootstrap": {
                "selected": "/tmp/notes",
                "project": "/tmp/notes/autocode-projects/build-wordcount-py-47003121",
            },
            "user_events": [{"kind": "goal_approval", "token": "t1"}],
        }
        rows = self.project(state, "continue")["messages"]
        self.assertEqual(chat_history.BOOTSTRAP_NOTICE, rows[-1]["id"])
        self.assertIn("autocode-projects/build-wordcount-py-47003121", rows[-1]["text"])
        del state["workspace_bootstrap"]
        self.assertEqual(
            ["Build wordcount.py", chat_history.APPROVED_PLAN],
            [row["text"] for row in self.project(state, "continue")["messages"]],
        )

    def test_an_empty_folder_that_became_the_project_says_so_after_approval(self):
        state = {
            "status": "RUNNING",
            "task": "Build it",
            "workspace_bootstrap": {"selected": "/tmp/empty", "project": "/tmp/empty"},
            "user_events": [{"kind": "goal_approval", "token": "t1"}],
        }
        rows = self.project(state, "continue")["messages"]
        self.assertEqual(chat_history.BOOTSTRAP_NOTICE, rows[-1]["id"])
        self.assertEqual(
            "/tmp/empty was not a Git repository, so I made it one; what I build is written there.", rows[-1]["text"]
        )


if __name__ == "__main__":
    unittest.main()


class ClarificationResolverOfferTests(unittest.TestCase):
    def test_a_clarification_with_a_question_takes_the_answer_path_not_the_bound_reply(self):
        # The needs table names --answer for a clarification that carries questions
        # (pause_command); the bound provide_information reply belongs to the
        # blocker/operational stops. A clarification must not hijack the answer flow.
        state = {"status": "WAITING_FOR_USER"}
        need = {
            "kind": "answer",
            "resolver_scope": "clarification",
            "resolver_request_id": "req-7",
            "resolver_token": "current-token",
            "questions": [{"id": "q1", "question": "Which language?", "options": ["Python", "Rust"]}],
        }
        public = {"status": state["status"], "needs": need}
        result = chat.project(state, public, workspace="/ws", run_dir="/run")
        self.assertNotEqual("resolver_response", (result["input"] or {}).get("kind"))
