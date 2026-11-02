"""A task-wide assessment carries evidence, never new execution authority."""

import copy
import json
import unittest
from unittest import mock

import autocode_chat_history as history
import autocode_plan_chat as chat
import autocode_requirements_handoff as handoff
import autocode_run_messages as messages
import autocode_util as util
from units import autoplanner as planner

from tests.test_run_messages import receipt, response, working


def context():
    return {
        "kind": "coordinated_task",
        "participants": [
            {"target": "gateway", "status": "AWAITING_GOAL_APPROVAL", "plan": {"intended_outcome": "Keep history"}},
            {"target": "reader", "status": "RUNNING", "plan": None},
        ],
    }


def routed(state=None):
    state = state or working()
    event = messages.receive(state, {**receipt(), "context": context()})
    messages.activate(state)
    return state, event


class TaskConversationContextTests(unittest.TestCase):
    def test_a_source_question_is_suspended_only_during_the_answer_and_restored_exactly(self):
        state = working()
        state.update(
            status="WAITING_FOR_USER",
            pending_questions=[{"id": "scope", "question": "Which scope?"}],
            user_request={"kind": "clarification"},
            resolver_human_proposal={"scope": "clarification"},
            resolver_human_request={"scope": "clarification", "request_id": "original"},
        )
        before = copy.deepcopy(state)
        state, event = routed(state)
        for key in ("pending_questions", "user_request", "resolver_human_proposal", "resolver_human_request"):
            self.assertNotIn(key, state)
        handoff.accept_chat(state, {**response(event), "affected_targets": []}, {}, queue=mock.Mock())
        for key in before.keys() - {"user_events"}:
            self.assertEqual(before[key], state[key], key)

    def test_operational_question_stays_held_and_new_questions_cannot_be_discarded(self):
        for key in ("resolver_human_request", "resolver_human_proposal"):
            state = working()
            state.update(status="WAITING_FOR_USER", **{key: {"scope": "operational_exhaustion"}})
            messages.receive(state, {**receipt(), "context": context()})
            before = copy.deepcopy(state)
            self.assertFalse(messages.activate(state))
            self.assertEqual(before, state)
        state, event = routed()
        state["pending_questions"] = [{"id": "new", "question": "New scope?"}]
        before = copy.deepcopy(state)
        with self.assertRaises(ValueError):
            handoff.accept_chat(state, {**response(event), "affected_targets": []}, {}, queue=mock.Mock())
        self.assertEqual(before, state)

    def test_context_is_copied_and_bound_to_the_exact_message(self):
        state = working()
        supplied = context()
        event = messages.receive(state, {**receipt(), "context": supplied})
        self.assertEqual(supplied, event.get("context"))
        supplied["participants"][0]["plan"]["intended_outcome"] = "Changed after receipt"
        self.assertEqual(context(), event["context"])
        self.assertEqual(event, messages.receive(state, {**receipt(), "context": context()}))
        for altered in (None, supplied):
            with self.subTest(altered=altered), self.assertRaisesRegex(ValueError, "different"):
                messages.receive(state, {**receipt(), **({"context": altered} if altered else {})})

    def test_context_rejects_unsupported_authority_and_unbounded_or_duplicate_targets(self):
        bad = [
            {**context(), "approve": True},
            {"kind": "coordinated_task", "participants": []},
            {"kind": "other", "participants": context()["participants"]},
            {"kind": "coordinated_task", "participants": context()["participants"] * 7},
            {"kind": "coordinated_task", "participants": [context()["participants"][0]] * 2},
            {"kind": "coordinated_task", "participants": [{**context()["participants"][0], "run_dir": "/outside"}]},
            {"kind": "coordinated_task", "participants": [{"target": "", "status": "RUNNING", "plan": None}]},
            {"kind": "coordinated_task", "participants": [{"target": "gateway", "status": "RUNNING", "plan": "x"}]},
            {
                "kind": "coordinated_task",
                "participants": [{"target": "gateway", "status": "RUNNING", "plan": {"huge": "x" * 131072}}],
            },
        ]
        for supplied in bad:
            with self.subTest(supplied=str(supplied)[:150]):
                state = working()
                before = copy.deepcopy(state)
                with self.assertRaisesRegex(ValueError, "context"):
                    messages.receive(state, {**receipt(), "context": supplied})
                self.assertEqual(before, state)

    def test_prompt_contains_current_public_participant_plans_as_separate_evidence(self):
        state, event = routed()
        prompt, _ = chat.context(state)
        packet = json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])
        self.assertEqual(receipt()["text"], packet["message"]["text"])
        self.assertEqual(context(), packet["message"]["context"])
        self.assertIn("affected_targets", prompt)
        self.assertIn("original human text", prompt)
        self.assertEqual(event["contract_token"], packet["message"]["contract_token"])

    def test_schema_requires_routing_only_for_a_task_context(self):
        state, event = routed()
        schema = planner.schema_for(state, "requirements_gather")
        answer = {**response(event), "affected_targets": []}
        util.validate_schema(answer, schema)
        with self.assertRaises(ValueError):
            util.validate_schema(response(event), schema)
        ordinary = working()
        regular = messages.receive(ordinary, receipt())
        messages.activate(ordinary)
        with self.assertRaises(ValueError):
            util.validate_schema(
                {**response(regular), "affected_targets": []}, planner.schema_for(ordinary, "requirements_gather")
            )

    def test_question_restores_checkpoint_and_exposes_bound_routing_receipt_in_public_history(self):
        state, event = routed()
        before = copy.deepcopy(state)
        queue = mock.Mock()
        handoff.accept_chat(state, {**response(event), "affected_targets": []}, {}, queue=queue)
        self.assertEqual(before["goal_contract"], state["goal_contract"])
        self.assertEqual(before["source_log"], state["source_log"])
        reply = next(row for row in history.messages(state) if row["id"] == event["id"] + "-reply-after")
        self.assertEqual(
            {
                "request_id": "m1",
                "intent": "question",
                "affected_targets": [],
                "context_sha256": util.digest(context()),
            },
            reply.get("conversation"),
        )
        queue.assert_not_called()

    def test_change_answers_but_leaves_local_scope_and_plan_for_coordinator_routing(self):
        state = working()
        state.update(status="WAITING_FOR_USER", stop_reason="Linked scope question")
        before = copy.deepcopy(state)
        state, event = routed(state)
        queue = mock.Mock()
        handoff.accept_chat(state, {**response(event, "change"), "affected_targets": ["gateway"]}, {}, queue=queue)
        for key in before.keys() - {"user_events"}:
            self.assertEqual(before[key], state[key], key)
        self.assertEqual(["gateway"], event["affected_targets"])
        self.assertNotIn("forwarded", event)
        queue.assert_not_called()
        self.assertFalse(messages.correction_admitted(state, event["text"]))

    def test_invalid_model_routing_is_rejected_before_recording_reply_or_restoring_state(self):
        for intent, targets in (
            ("change", []),
            ("question", ["gateway"]),
            ("change", ["/outside"]),
            ("change", ["gateway", "gateway"]),
            ("change", "gateway"),
        ):
            with self.subTest(intent=intent, targets=targets):
                state, event = routed()
                before = copy.deepcopy(state)
                with self.assertRaises(ValueError):
                    handoff.accept_chat(
                        state, {**response(event, intent), "affected_targets": targets}, {}, queue=mock.Mock()
                    )
                self.assertEqual(before, state)


if __name__ == "__main__":
    unittest.main()
