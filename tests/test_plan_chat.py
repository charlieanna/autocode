"""Questions about a reviewed plan do not silently become requirements."""

import copy
import json
import unittest
from pathlib import Path
from unittest import mock

import autocode_chat_history as history
import autocode_chat_view as chat_view
import autocode_goals as goals
import autocode_requirements_chat as requirements_chat
import autocode_requirements_handoff as handoff
import autocode_source_log as source_log
import autocode_util as util
import autopilot
from goal_fixtures import body
from units import autoplanner as planner

WHY = "Why did you choose this approach?"
REPLY = "The plan reuses the existing totals calculation and keeps text output unchanged."


def reviewed():
    contract = {
        "task_id": "plan-chat-task",
        "revision": 3,
        "body": body(),
        "approval_status": "draft",
        "approval_event": None,
    }
    contract["hash"] = util.digest({key: contract[key] for key in ("task_id", "revision", "body")})
    selected = goals.token(contract)
    state = {
        "task_id": contract["task_id"],
        "task": "Add JSON output",
        "workspace": "/fixture",
        "settings": {
            "spec_first": True,
            "joint_planning": True,
            "roles": {"requirements": {"model": "saved-model", "engine": "codex"}},
        },
        "status": "AWAITING_GOAL_APPROVAL",
        "phase": "AWAITING_GOAL_APPROVAL",
        "next_stage": "astra_plan",
        "goal_contract": contract,
        "displayed_goal": selected,
        "displayed_handoff": "none",
        "planning": {"final_token": selected, "astra_calls": 2, "reports": {}},
        "answers": {},
        "user_events": [],
        "brief_feedback": [],
        "pending_questions": [],
        "stages": [],
    }
    source_log.seed(state)
    return state


def pending():
    state = reviewed()
    state["user_events"].append(
        {
            "kind": "plan_message",
            "actor": "user_cli",
            "id": "plan-message-1",
            "text": WHY,
            "contract_token": state["displayed_goal"],
            "return_stage": "astra_plan",
        }
    )
    state.update(status="RUNNING", phase="PLANNING", next_stage="requirements_gather")
    return state


def response(state, intent="question", **changes):
    event = state["user_events"][-1]
    return {
        "message_id": event["id"],
        "contract_token": event["contract_token"],
        "intent": intent,
        "assistant_reply": REPLY,
        **changes,
    }


class PlanMessageTests(unittest.TestCase):
    def test_feedback_at_plan_approval_defers_interpretation_without_changing_the_plan(self):
        state = reviewed()
        before = copy.deepcopy(state)
        goals.feedback(state, WHY)
        self.assertEqual(before["goal_contract"], state["goal_contract"])
        self.assertEqual(before["displayed_goal"], state.get("displayed_goal"))
        self.assertEqual(before["planning"], state["planning"])
        self.assertEqual(before["source_log"], state["source_log"])
        self.assertEqual([], state["brief_feedback"])
        self.assertEqual({}, state["answers"])
        self.assertEqual(("RUNNING", "requirements_gather"), (state["status"], state["next_stage"]))
        self.assertEqual("plan_message", state["user_events"][-1]["kind"])
        self.assertNotIn(WHY, goals.scan_texts(state))

    def test_begin_is_limited_to_a_current_displayed_chat_plan(self):
        import autocode_plan_chat as chat

        for change in ({"settings": {}}, {"status": "WAITING_FOR_USER"}, {"goal_contract": None}):
            state = {**reviewed(), **change}
            before = copy.deepcopy(state)
            self.assertFalse(chat.begin(state, WHY))
            self.assertEqual(before, state)
        for change in (
            {"displayed_goal": "stale"},
            {"displayed_handoff": "stale"},
            {"active_stage": {"stage": "terra"}},
        ):
            state = {**reviewed(), **change}
            before = copy.deepcopy(state)
            with self.assertRaises(ValueError):
                chat.begin(state, WHY)
            self.assertEqual(before, state)
        state = reviewed()
        with self.assertRaises(ValueError):
            chat.begin(state, " ")
        state["goal_contract"]["body"]["intended_outcome"] = "unsealed edit"
        with self.assertRaises(ValueError):
            chat.begin(state, WHY)

    def test_pending_finds_only_an_unanswered_plan_message(self):
        import autocode_plan_chat as chat

        state = pending()
        event = state["user_events"][-1]
        self.assertIs(event, chat.pending(state))
        state["user_events"].append({"kind": "timeout_response", "text": "receipt"})
        self.assertIs(event, chat.pending(state))
        event["reply_after"] = REPLY
        self.assertIsNone(chat.pending(state))
        self.assertIsNone(chat.pending(reviewed()))

    def test_question_response_preserves_sources_and_returns_to_the_same_plan(self):
        import autocode_plan_chat as chat

        state = pending()
        before = copy.deepcopy(state)
        event = chat.accept(state, response(state), {"output": "reply.json"})
        self.assertEqual(REPLY, event["reply_after"])
        self.assertEqual("question", event["intent"])
        self.assertEqual(("AWAITING_GOAL_APPROVAL", "astra_plan"), (state["status"], state["next_stage"]))
        for key in ("goal_contract", "planning", "displayed_goal", "source_log", "answers", "brief_feedback"):
            self.assertEqual(before[key], state[key], key)
        self.assertIsNone(chat.pending(state))
        self.assertEqual([state["task"], WHY, REPLY], [row["text"] for row in history.messages(state)])
        with self.assertRaises(ValueError):
            chat.accept(state, response(before), {"output": "duplicate.json"})

    def test_stale_or_invalid_replies_are_rejected_without_mutation(self):
        import autocode_plan_chat as chat

        for change in (
            {"message_id": "old"},
            {"contract_token": "old"},
            {"intent": "approve"},
            {"assistant_reply": " "},
            {"assistant_reply": "x" * 12001},
            {"contract": {"approval_status": "approved"}},
        ):
            state = pending()
            before = copy.deepcopy(state)
            with self.subTest(change=list(change)), self.assertRaises(ValueError):
                chat.accept(state, response(state, **change), {})
            self.assertEqual(before, state)
        state = pending()
        report = response(state)
        state["goal_contract"]["revision"] += 1
        with self.assertRaises(ValueError):
            chat.accept(state, report, {})

    def test_pending_message_is_visible_and_can_stream_on_the_requirements_route(self):
        state = pending()
        self.assertTrue(requirements_chat.enabled(state))
        self.assertEqual([state["task"], WHY], [row["text"] for row in history.messages(state)])
        self.assertFalse(requirements_chat.enabled(reviewed()))
        result = chat_view.project(
            reviewed(),
            {"status": "AWAITING_GOAL_APPROVAL", "needs": {"kind": "approve_plan"}},
            run_dir="/run",
            workspace="/fixture",
        )
        self.assertEqual("Send message", result["input"]["label"])

    def test_prompt_and_schema_bind_the_message_to_the_reviewed_plan(self):
        import autocode_plan_chat as chat

        state = pending()
        prompt, metrics = chat.context(state)
        rules, data = prompt.split("\nCURRENT HANDOFF DATA\n", 1)
        packet = json.loads(data)
        self.assertEqual(state["goal_contract"], packet["goal_contract"])
        self.assertEqual(WHY, packet["message"]["text"])
        self.assertEqual(state["displayed_goal"], packet["message"]["contract_token"])
        self.assertIn("mixed", rules.lower())
        self.assertIn("approval", rules.lower())
        self.assertGreater(metrics["estimated_prompt_tokens"], 0)
        self.assertEqual(10000, metrics["soft_budget_tokens"])
        self.assertEqual((prompt, metrics), planner.context(state, "requirements_gather", Path("/fixture/state.json")))
        schema = planner.schema_for(state, "requirements_gather")
        util.validate_schema(response(state), schema)
        self.assertEqual({"message_id", "contract_token", "intent", "assistant_reply"}, set(schema["properties"]))

    def test_report_dispatch_republishes_approval_without_redrafting(self):
        state = pending()
        before = copy.deepcopy(state)
        with (
            mock.patch.object(autopilot.human, "queue") as queue,
            mock.patch.object(autopilot.clarification, "clarify_discoverable") as clarify,
        ):
            autopilot.apply_planning(state, "requirements_gather", response(state), {"output": None})
        clarify.assert_not_called()
        self.assertEqual("goal_approval", queue.call_args.args[1])
        self.assertEqual("AWAITING_GOAL_APPROVAL", queue.call_args.kwargs["status"])
        self.assertEqual(before["goal_contract"], state["goal_contract"])
        self.assertEqual(before["planning"], state["planning"])

    def test_a_real_or_mixed_correction_still_invalidates_and_requires_new_review(self):
        state = pending()
        text = "Why this format? Change empty JSON output to [] instead."
        state["user_events"][-1]["text"] = text
        before = copy.deepcopy(state["goal_contract"])
        self.assertTrue(handoff.accept_chat(state, response(state, "change"), {"output": None}, queue=mock.Mock()))
        self.assertEqual("RUNNING", state["status"])
        self.assertEqual("draft", state["goal_contract"]["approval_status"])
        self.assertIsNone(state.get("displayed_goal"))
        self.assertEqual(before["body"], state["goal_contract"]["body"])
        self.assertEqual(text, state["brief_feedback"][-1]["text"])
        self.assertIn(text, goals.scan_texts(state))
        rows = [row["text"] for row in history.messages(state)]
        self.assertEqual(1, rows.count(text))
        self.assertEqual(REPLY, rows[-1])
        self.assertIsNone(state["goal_contract"]["approval_event"])

    def test_rendering_a_pending_exchange_preserves_the_display_but_offers_no_approval(self):
        import autocode_goal_lifecycle as lifecycle

        state = pending()
        selected = state["displayed_goal"]
        with (
            mock.patch.object(lifecycle.human, "current", return_value=None),
            mock.patch.object(lifecycle, "render", return_value="working"),
            mock.patch.object(lifecycle.base_examples, "check"),
            mock.patch.object(lifecycle.interaction_timing, "presented"),
        ):
            lifecycle.present(state)
        self.assertEqual(selected, state.get("displayed_goal"))
        self.assertIsNotNone(lifecycle.approval_boundary_refusal(state))

    def test_conversation_action_acknowledges_the_plan_message_once(self):
        import autocode_conversation_policy as policy

        before = reviewed()
        candidate = pending()
        action = {
            "request_id": "question-request",
            "digest": "a" * 64,
            "conversation_id": "chat",
            "handoff_digest": "b" * 64,
            "revision": 1,
            "message_ids": ["message"],
        }
        policy.acknowledge(before, candidate, action)
        receipt = candidate["user_events"][-1]
        self.assertEqual("plan_message", receipt["source_events"][0]["kind"])

    def test_regular_requirements_reports_do_not_enter_plan_chat(self):
        before = reviewed()
        state = copy.deepcopy(before)
        self.assertFalse(handoff.accept_chat(state, {}, {}, queue=mock.Mock()))
        self.assertEqual(before, state)


if __name__ == "__main__":
    unittest.main()
