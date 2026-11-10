"""A live question is a saved conversation, not automatic plan feedback."""

import copy
import unittest
from unittest import mock

import autocode_goals as goals
import autocode_plan_chat as plan_chat
import autocode_requirements_handoff as handoff
import autocode_run_messages as messages

from tests.test_plan_chat import acceptance_hold, reviewed


def working():
    state = reviewed()
    state.update(status="RUNNING", phase="PLANNING", next_stage="finalize_plan")
    return state


def executing():
    state = acceptance_hold()
    state.update(status="RUNNING", phase="EXECUTING", next_stage="builder")
    state.pop("stop_reason", None)
    return state


def receipt(identifier="m1", text="Could we reuse the existing XML-RPC/EPP conversion?"):
    return {"id": identifier, "kind": "message", "text": text, "submitted_at": "now"}


def response(event, intent="question"):
    return {
        "message_id": event["id"],
        "contract_token": event["contract_token"],
        "intent": intent,
        "assistant_reply": "That is a design option to assess; it is not an implementation decision yet.",
    }


class LiveConversationTests(unittest.TestCase):
    def test_resume_projection_matches_activation_but_keeps_discussion_paused(self):
        for context, stage in ((None, "requirements_gather"), ({"kind": "coordinated_plan"}, "finalize_plan")):
            with self.subTest(context=context):
                state = working()
                state.update(status="PAUSED_INTERVENTION", next_stage="answer_question")
                event = messages.receive(state, receipt(text="Please fix it."))
                if context:
                    event["context"] = context
                    state["settings"]["spec_first"] = False
                before = copy.deepcopy(state)
                self.assertEqual(stage, messages.resume_stage(state))
                self.assertFalse(messages.activate(state))
                self.assertEqual(before, state)
                state["status"] = "RUNNING"
                self.assertTrue(messages.activate(state))
                self.assertEqual(stage, state["next_stage"])
                self.assertEqual("answer_question", event["return_stage"])

    def test_resume_projection_does_not_promise_a_protected_handoff(self):
        for change in (
            {"status": "PAUSED_JOB_FAILURE"},
            {"pause_intent": {"held_pause": {"status": "PAUSED_COST_LIMIT"}}},
            {"applied_interventions": [{"kind": "stop"}]},
            {"active_stage": {"stage": "answer_question"}},
            {"uncertain_artifacts": ["attempt"]},
            {"pending_report_repair": {"stage": "answer_question"}},
            {"resolver_human_proposal": {"scope": "operational_exhaustion"}},
            {"resolver_human_request": {"scope": "operational_exhaustion"}},
            {"user_events": [{"kind": "plan_message"}]},
            {"settings": {"spec_first": False}},
            {"goal_contract": {"hash": "invalid", "task_id": "t", "revision": 1, "body": {}}},
            {"goal_contract": {"hash": "incomplete"}},
        ):
            with self.subTest(change=change):
                state = working()
                state.update(status="PAUSED_INTERVENTION", next_stage="answer_question")
                state.update(change)
                messages.receive(state, receipt())
                before = copy.deepcopy(state)
                self.assertIsNone(messages.resume_stage(state))
                self.assertEqual(before, state)
        state = working()
        state["status"] = "PAUSED_INTERVENTION"
        self.assertIsNone(messages.resume_stage(state))

    def test_message_suspends_and_restores_a_clarification_rebound_after_settings_change(self):
        for intent in ("question", "change"):
            state = working()
            state.update(
                status="RESOLVER_PENDING",
                phase="DISCOVERING",
                next_stage="draft_plan",
                resolver_human_proposal={"scope": "clarification", "identity": "new-settings"},
                pending_questions=[{"id": "scope", "question": "Which behavior?"}],
            )
            before = copy.deepcopy(state)
            event = messages.receive(state, receipt())
            self.assertTrue(messages.activate(state))
            self.assertNotIn("resolver_human_proposal", state)
            messages.accept(state, response(event, intent), {})
            for key in before.keys() - {"user_events"}:
                self.assertEqual(before[key], state[key], key)
            self.assertEqual(intent == "change", messages.correction_admitted(state, event["text"]))

    def test_pending_unknown_or_operational_resolver_never_grants_conversation_admission(self):
        for proposal in (None, {}, {"scope": "blocker"}, {"scope": "operational_exhaustion"}):
            state = working()
            state.update(status="RESOLVER_PENDING", resolver_human_proposal=proposal)
            messages.receive(state, receipt())
            before = copy.deepcopy(state)
            self.assertFalse(messages.activate(state))
            self.assertEqual(before, state)
        state = working()
        event = messages.receive(state, receipt())
        messages.activate(state)
        event["return_checkpoint"].update(
            status="RESOLVER_PENDING", resolver_human_proposal={"scope": "operational_exhaustion"}
        )
        with self.assertRaises(ValueError):
            messages.accept(state, response(event), {})

    def test_receiving_a_message_is_idempotent_and_does_not_interrupt_or_change_the_plan(self):
        state = working()
        state["active_stage"] = {"stage": "review_plan"}
        before = copy.deepcopy(state)
        first = messages.receive(state, receipt())
        self.assertEqual(first, messages.receive(state, receipt()))
        self.assertEqual(1, len(state["user_events"]))
        for key in before.keys() - {"user_events"}:
            self.assertEqual(before[key], state[key], key)
        self.assertFalse(messages.activate(state))
        with self.assertRaisesRegex(ValueError, "different"):
            messages.receive(state, receipt(text="Change everything"))
        self.assertEqual(first["text"], state["user_events"][0]["text"])

    def test_boundary_conversation_uses_requirements_without_invalidating_existing_work(self):
        state = working()
        before = copy.deepcopy(state)
        event = messages.receive(state, receipt())
        self.assertTrue(messages.activate(state))
        self.assertEqual("requirements_gather", state["next_stage"])
        self.assertEqual("plan_message", event["kind"])
        self.assertTrue(event["live"])
        self.assertEqual("finalize_plan", event["return_checkpoint"]["next_stage"])
        for key in ("goal_contract", "planning", "source_log", "brief_feedback", "settings", "stages"):
            self.assertEqual(before[key], state[key], key)
        self.assertFalse(messages.activate(state), "never launch a second conversation over an active one")

    def test_answer_restores_the_exact_checkpoint_and_keeps_the_reply(self):
        for status, phase in (
            ("RUNNING", "EXECUTING"),
            ("AWAITING_GOAL_APPROVAL", "AWAITING_GOAL_APPROVAL"),
            ("WAITING_FOR_USER", "PLANNING"),
        ):
            with self.subTest(status=status):
                state = working()
                state.update(status=status, phase=phase, stop_reason="retained explanation")
                before = copy.deepcopy(state)
                event = messages.receive(state, receipt())
                messages.activate(state)
                accepted = messages.accept(state, response(event), {"output": "reply.json"})
                self.assertEqual("question", accepted["intent"])
                self.assertTrue(accepted["reply_after"])
                for key in before.keys() - {"user_events"}:
                    self.assertEqual(before[key], state[key], key)

    def test_operational_holds_and_uncertain_work_keep_messages_queued(self):
        for change in (
            {"status": "PAUSED_COST_LIMIT"},
            {"status": "PAUSED_INTERVENTION"},
            {"uncertain_artifacts": ["attempt"]},
            {"pending_report_repair": {"stage": "finalize_plan"}},
        ):
            with self.subTest(change=change):
                state = {**working(), **change}
                messages.receive(state, receipt())
                before = copy.deepcopy(state)
                self.assertFalse(messages.activate(state))
                self.assertEqual(before, state)

    def test_message_accepted_before_completion_is_answered_then_restores_completion(self):
        state = working()
        event = messages.receive(state, receipt())
        state.update(
            status="TASK_COMPLETE",
            phase="COMPLETE",
            next_stage=None,
            completed_at="finished",
            completion_actor="reviewer",
            final_decision={"status": "COMPLETE"},
        )
        before = copy.deepcopy(state)
        self.assertTrue(messages.activate(state))
        self.assertNotIn("completed_at", state)
        self.assertNotIn("final_decision", state)
        messages.accept(state, response(event), {})
        for key in before.keys() - {"user_events"}:
            self.assertEqual(before[key], state[key], key)

    def test_messages_are_answered_in_submission_order_without_repeating_finished_answers(self):
        state = working()
        first = messages.receive(state, receipt())
        second = messages.receive(state, receipt("m2", "And what are the tradeoffs?"))
        messages.activate(state)
        self.assertEqual("run_message", second["kind"])
        messages.accept(state, response(first), {})
        self.assertTrue(messages.activate(state))
        self.assertEqual("plan_message", second["kind"])
        messages.accept(state, response(second), {})
        self.assertFalse(messages.activate(state))
        self.assertEqual(first, messages.receive(state, receipt()))
        self.assertFalse(messages.activate(state))

    def test_a_changed_contract_or_forged_reply_cannot_restore_a_saved_checkpoint(self):
        for mutation in ("contract", "message", "hold"):
            with self.subTest(mutation=mutation):
                state = working()
                event = messages.receive(state, receipt())
                messages.activate(state)
                reply = response(event)
                if mutation == "contract":
                    state["goal_contract"]["body"]["intended_outcome"] = "unreviewed change"
                elif mutation == "message":
                    reply["message_id"] = "another-message"
                else:
                    event["return_checkpoint"]["status"] = "PAUSED_COST_LIMIT"
                before = copy.deepcopy(state)
                with self.assertRaises(ValueError):
                    messages.accept(state, reply, {})
                self.assertEqual(before, state)

    def test_discussion_before_a_plan_exists_preserves_the_requirements_frontier(self):
        state = working()
        state.pop("goal_contract")
        state["next_stage"] = "draft_plan"
        event = messages.receive(state, receipt())
        self.assertTrue(messages.activate(state))
        messages.accept(state, response(event), {})
        self.assertNotIn("goal_contract", state)
        self.assertEqual("draft_plan", state["next_stage"])
        self.assertEqual([], state["brief_feedback"])

    def test_context_contains_user_message_current_work_and_prior_exchanges(self):
        state = working()
        messages.receive(state, receipt())
        messages.activate(state)
        packet = messages.context(state)
        self.assertEqual("finalize_plan", packet["message"]["return_checkpoint"]["next_stage"])
        self.assertEqual(receipt()["text"], packet["message"]["text"])
        self.assertEqual(state["goal_contract"], packet["goal_contract"])
        self.assertEqual(state["task"], packet["task"])
        self.assertEqual(state.get("requirements_handoff"), packet["requirements_handoff"])

    def test_an_operational_hold_arriving_during_discussion_cannot_be_overwritten(self):
        state = working()
        event = messages.receive(state, receipt())
        messages.activate(state)
        state.update(status="PAUSED_COST_LIMIT", phase="PAUSED_OR_BLOCKED")
        before = copy.deepcopy(state)
        with self.assertRaises(ValueError):
            messages.accept(state, response(event), {})
        self.assertEqual(before, state)


class LiveConversationRoutingTests(unittest.TestCase):
    def test_a_later_queued_message_does_not_prevent_applying_the_first_answered_change(self):
        state = executing()
        first = messages.receive(state, receipt(text="Please preserve the registry result."))
        second = messages.receive(state, receipt("m2", "What are the tradeoffs?"))
        messages.activate(state)
        self.assertTrue(handoff.accept_chat(state, response(first, "change"), {}, queue=mock.Mock()))
        self.assertEqual(first["text"], state["brief_feedback"][-1]["text"])
        self.assertTrue(first["forwarded"])
        self.assertEqual("run_message", second["kind"])
        self.assertEqual("draft", state["goal_contract"]["approval_status"])
        self.assertIsNone(state["goal_contract"]["approval_event"])

    def test_change_accepted_before_completion_archives_completion_and_requires_review(self):
        state = working()
        event = messages.receive(state, receipt(text="Please change the output."))
        state.update(
            status="TASK_COMPLETE",
            phase="COMPLETE",
            next_stage=None,
            completed_at="finished",
            completion_actor="reviewer",
            final_decision={"status": "COMPLETE"},
        )
        self.assertTrue(messages.activate(state))
        self.assertTrue(handoff.accept_chat(state, response(event, "change"), {}, queue=mock.Mock()))
        self.assertEqual("RUNNING", state["status"])
        self.assertNotIn("completed_at", state)
        self.assertNotIn("final_decision", state)
        self.assertNotIn("completion_actor", state)
        self.assertEqual("finished", state["completion_archive"][-1]["completed_at"])
        self.assertEqual("draft", state["goal_contract"]["approval_status"])

    def test_unanswered_questions_different_text_and_replayed_changes_grant_no_feedback_admission(self):
        for kind in ("unanswered", "question", "different", "replayed"):
            with self.subTest(kind=kind):
                # Idle unapproved planning now accepts fresh feedback (#2670).
                # Test conversation authority at approved work instead.
                state = executing()
                event = messages.receive(state, receipt())
                messages.activate(state)
                if kind != "unanswered":
                    messages.accept(state, response(event, "question" if kind == "question" else "change"), {})
                if kind == "replayed":
                    event["forwarded"] = True
                text = "Different correction" if kind == "different" else receipt()["text"]
                before = copy.deepcopy(state)
                with self.assertRaises(ValueError):
                    goals.feedback(state, text, interpret=False)
                self.assertEqual(before, state)

    def test_live_question_returns_to_work_without_a_new_approval_or_requirements_cycle(self):
        state = working()
        before = copy.deepcopy(state)
        event = messages.receive(state, receipt())
        messages.activate(state)
        queue = mock.Mock()
        self.assertTrue(handoff.accept_chat(state, response(event), {}, queue=queue))
        queue.assert_not_called()
        for key in before.keys() - {"user_events"}:
            self.assertEqual(before[key], state[key], key)

    def test_live_context_can_discuss_work_before_a_plan_exists(self):
        state = working()
        state.pop("goal_contract")
        messages.receive(state, receipt())
        messages.activate(state)
        prompt, metrics = plan_chat.context(state)
        self.assertIn(receipt()["text"], prompt)
        self.assertIn('"next_stage": "finalize_plan"', prompt)
        self.assertIn("read-only", prompt)
        self.assertIn("clear, correctly spelled English", prompt)
        self.assertIn("instead of quoting their typos or shorthand", prompt)
        self.assertIn("source_quote evidence verbatim", prompt)
        self.assertGreater(metrics["estimated_prompt_tokens"], 0)

    def test_explicit_change_keeps_the_reply_and_original_text_but_revokes_old_approval(self):
        state = working()
        approval = {"kind": "goal_approval", "actor": "user_cli", "token": state["displayed_goal"]}
        state["goal_contract"].update(approval_status="approved", approval_event=approval)
        state["user_events"].append(approval)
        original = "Please change the output to preserve the registry result."
        event = messages.receive(state, receipt(text=original))
        messages.activate(state)
        queue = mock.Mock()
        self.assertTrue(handoff.accept_chat(state, response(event, "change"), {}, queue=queue))
        self.assertEqual("draft", state["goal_contract"]["approval_status"])
        self.assertIsNone(state["goal_contract"]["approval_event"])
        self.assertEqual(original, state["brief_feedback"][-1]["text"])
        self.assertEqual(event["reply_after"], state["brief_feedback"][-1]["reply_after"])
        self.assertTrue(event["forwarded"])
        self.assertEqual(2, state["planning"]["astra_calls"])
        queue.assert_not_called()
