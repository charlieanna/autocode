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
        self.assertEqual("I couldn’t finish this step. The run’s details say what needs attention.", text)
        self.assertNotIn("Run details", text)

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

        for stage in roles.ALL_PLANNING_STAGES:
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
        for stage in (None, "unknown", "terra", "sol", "astra_review", "investigate_bug"):
            with self.subTest(stage=stage):
                state = {"status": "PAUSED_INVALID_OUTPUT", "next_stage": stage}
                self.assertIsNone(self.project(state, "resume")["input"])
        for request in ({"kind": "human_review"}, {"kind": "blocker"}):
            state = {"status": "PAUSED_INVALID_OUTPUT", "next_stage": "astra_finalize", "user_request": request}
            self.assertIsNone(self.project(state, "resume")["input"])

    def test_invalid_planning_report_does_not_release_held_operational_authority(self):
        import autocode_goals as goals

        state = {
            "status": "PAUSED_INVALID_OUTPUT",
            "next_stage": "astra_finalize",
            goals.RESOLVER_PROPOSAL_KEY: {
                "scope": "operational_exhaustion",
                "origin": {"pause_status": "PAUSED_TIME_LIMIT"},
            },
        }
        self.assertIsNone(self.project(state, "resume")["input"])

    def test_a_retained_invalid_report_does_not_offer_feedback_while_running(self):
        state = {"status": "PAUSED_INVALID_OUTPUT", "next_stage": "astra_finalize"}
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
                "AUTOCODE_RESOLVER_TOKEN=current-token",
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
        for missing in ("resolver_request_id", "resolver_token"):
            invalid = {**public, "needs": {k: v for k, v in need.items() if k != missing}}
            self.assertIsNone(chat.project(state, invalid, workspace="/ws", run_dir="/run")["input"])
        for change in ({"route": {"role": "sol"}}, {"resolver_scope": "requirements"}):
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
                self.assertIn("AUTOCODE_RESOLVER_TOKEN=current-token", result["input"]["next_command"])
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
            "questions": [{"id": "route-glm", "question": question, "options": []}],
        }
        routed = {
            **need,
            "route": {
                "question_id": "route-glm",
                "role": "glm",
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

    def test_a_bootstrapped_project_is_named_in_the_conversation_once(self):
        state = {
            "status": "RUNNING",
            "task": "Build wordcount.py",
            "workspace_bootstrap": {
                "selected": "/tmp/notes",
                "project": "/tmp/notes/autocode-projects/build-wordcount-py-47003121",
            },
        }
        rows = self.project(state, "continue")["messages"]
        self.assertEqual(["user", "assistant"], [row["role"] for row in rows])
        self.assertEqual(chat_history.BOOTSTRAP_NOTICE, rows[1]["id"])
        self.assertIn("/tmp/notes is not a Git repository", rows[1]["text"])
        self.assertIn("autocode-projects/build-wordcount-py-47003121", rows[1]["text"])
        # One disclosure for the whole run: a later turn does not repeat it.
        state["user_events"] = [{"kind": "brief_feedback", "id": "f1", "text": "Add a test"}]
        later = self.project(state, "continue")["messages"]
        self.assertEqual(chat_history.BOOTSTRAP_NOTICE, later[1]["id"])
        self.assertEqual(1, sum(row["id"] == chat_history.BOOTSTRAP_NOTICE for row in later))
        self.assertIn("Add a test", [row["text"] for row in later][-1])

    def test_an_empty_folder_that_became_the_project_says_so_and_a_repository_says_nothing(self):
        state = {
            "status": "RUNNING",
            "task": "Build it",
            "workspace_bootstrap": {"selected": "/tmp/empty", "project": "/tmp/empty"},
        }
        rows = self.project(state, "continue")["messages"]
        self.assertEqual(chat_history.BOOTSTRAP_NOTICE, rows[1]["id"])
        self.assertIn("was not a Git repository", rows[1]["text"])
        self.assertIn("/tmp/empty", rows[1]["text"])
        del state["workspace_bootstrap"]
        self.assertEqual(["Build it"], [row["text"] for row in self.project(state, "continue")["messages"]])


if __name__ == "__main__":
    unittest.main()
