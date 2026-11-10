"""The responsible agent interprets gate replies; exact core actions still own consent."""

import copy
import importlib
import json
import unittest
from unittest import mock

import autocode_goals as goals
import autocode_requirements_review as review
import autocode_run_status as run_status
import autocode_util as util
from goal_fixtures import body
from units import autoplanner as planner


def requirements():
    state = {
        "task": "Add JSON output",
        "workspace": "/fixture",
        "settings": {"spec_first": True, "roles": {"requirements": {}, "plan_reviewer": {}}},
        "status": "WAITING_FOR_USER",
        "phase": "DISCOVERING",
        "next_stage": "draft_plan",
        "requirements_handoff": {
            "report": {
                "job": "build",
                "intended_outcome": "JSON output",
                "required_behaviors": ["Output JSON when requested"],
                "acceptance_tests": ["JSON output parses"],
                "open_questions": [],
            },
            "output": None,
        },
        "user_events": [],
        "answers": {},
        "brief_feedback": [],
    }
    state["pending_questions"] = [review.review_question(state)]
    return state


def plan():
    state = requirements()
    contract = {
        "task_id": "gate-task",
        "revision": 3,
        "body": body(),
        "approval_status": "draft",
        "approval_event": None,
    }
    contract["hash"] = util.digest({key: contract[key] for key in ("task_id", "revision", "body")})
    state.update(
        status="AWAITING_GOAL_APPROVAL",
        phase="AWAITING_GOAL_APPROVAL",
        next_stage="plan_task",
        goal_contract=contract,
        displayed_goal=goals.token(contract),
        displayed_handoff=util.digest(state["requirements_handoff"]["report"])[:12],
        pending_questions=[],
    )
    return state


class GateRoutingRegressionTests(unittest.TestCase):
    def test_returning_to_requirements_waiting_retires_stale_stop_text(self):
        state = {"status": "RUNNING", "stop_reason": "old stop"}
        run_status.waiting_for_user(state, "DISCOVERING", next_stage="draft_plan")
        self.assertEqual("WAITING_FOR_USER", state["status"])
        self.assertEqual("DISCOVERING", state["phase"])
        self.assertNotIn("stop_reason", state)
        with self.assertRaises(TypeError):
            run_status.waiting_for_user(state, "DISCOVERING", status="RUNNING")

    def test_free_text_confirmation_is_read_without_rewriting_the_requirements(self):
        state = requirements()
        previous = copy.deepcopy(state["requirements_handoff"])
        goals.feedback(state, "yes coplete")
        self.assertEqual("gate_message", state["user_events"][-1]["kind"])
        self.assertEqual("requirements_gather", state["next_stage"])
        self.assertEqual([], state["brief_feedback"])
        self.assertEqual(previous, state["requirements_handoff"])

    def test_plan_reply_is_read_by_the_plan_reviewer_without_a_second_review(self):
        state = plan()
        previous = copy.deepcopy(state["goal_contract"])
        goals.feedback(state, "yes this plan is complete")
        self.assertEqual("finalize_plan", state["next_stage"])
        self.assertEqual("plan_reviewer", planner.route_for(state, state["next_stage"]))
        self.assertEqual(previous, state["goal_contract"])
        self.assertEqual([], state["brief_feedback"])


class GateChatTests(unittest.TestCase):
    def setUp(self):
        self.chat = importlib.import_module("autocode_gate_chat")

    def response(self, state, intent="confirm", **changes):
        event = self.chat.pending(state)
        return {
            "message_id": event["id"],
            "gate_token": event["gate_token"],
            "intent": intent,
            "assistant_reply": "Your reply confirms the displayed understanding.",
            **changes,
        }

    def test_the_model_receives_the_literal_reply_and_exact_displayed_evidence(self):
        for factory, stage in ((requirements, "requirements_gather"), (plan, "finalize_plan")):
            state = factory()
            self.assertTrue(self.chat.begin(state, "yes coplete"))
            prompt, _ = self.chat.context(state)
            packet = json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])
            self.assertEqual("yes coplete", packet["message"]["text"])
            self.assertEqual(stage, packet["message"]["stage"])
            self.assertEqual(stage, packet["stage"])
            self.assertEqual(state["requirements_handoff"], packet["requirements_handoff"])
            self.assertEqual(state.get("goal_contract"), packet["goal_contract"])
            schema = self.chat.schema(state)
            self.assertEqual([packet["message"]["id"]], schema["properties"]["message_id"]["enum"])
            self.assertEqual([packet["message"]["gate_token"]], schema["properties"]["gate_token"]["enum"])
            self.assertEqual(schema, planner.schema_for(state, stage))
            with mock.patch.object(planner, "workspace_inventory", side_effect=AssertionError("No repository tour")):
                self.assertEqual((prompt, _), planner.context(state, stage, None))

    def test_plan_conversation_does_not_consume_another_full_plan_review_allowance(self):
        state = plan()
        state["planning"] = {"astra_calls": 2, "review_call_limit": 2, "reports": {}}
        self.chat.begin(state, "yes coplete")
        before = copy.deepcopy(state["planning"])
        planner.charge(state, "finalize_plan", {})
        self.assertEqual(before, state["planning"])

    def test_stage_result_restores_the_gate_and_projects_the_interpretation(self):
        import autocode_chat_view as chat_view
        import autopilot

        for factory in (requirements, plan):
            state = factory()
            self.chat.begin(state, "yes coplete")
            stage = state["next_stage"]
            queue = mock.Mock()
            with mock.patch.object(autopilot.human, "queue", queue):
                autopilot.apply_planning(state, stage, self.response(state), {"output": None})
            queue.assert_called_once()
            public = {"status": state["status"], "needs": {"kind": "approve_plan"}}
            projected = chat_view.project(state, public, run_dir="/run", workspace="/fixture")
            self.assertEqual(self.chat.offer(state), projected["gate"])

    def test_a_previously_answered_business_question_does_not_block_the_current_review(self):
        state = requirements()
        state["requirements_handoff"]["report"]["open_questions"] = [{"id": "Q1", "question": "New entries only?"}]
        state["answers"]["Q1"] = {"text": "Only new"}
        state["pending_questions"] = [review.review_question(state)]
        self.assertTrue(self.chat.begin(state, "yes coplete"))

    def test_a_question_or_decline_preserves_the_gate_without_a_confirmation(self):
        for factory in (requirements, plan):
            for intent in ("question", "decline"):
                state = factory()
                before = copy.deepcopy(state)
                self.chat.begin(state, "What will happen next?")
                self.chat.accept(state, self.response(state, intent), {"output": None})
                self.assertEqual(before.get("goal_contract"), state.get("goal_contract"))
                self.assertEqual(before["requirements_handoff"], state["requirements_handoff"])
                self.assertEqual(before["status"], state["status"])
                self.assertIsNone(self.chat.offer(state)["decision"])

    def test_confirmation_is_an_interpretation_for_the_same_gate_not_an_approval(self):
        for factory in (requirements, plan):
            state = factory()
            before = copy.deepcopy(state)
            self.chat.begin(state, "yes coplete")
            self.chat.accept(state, self.response(state), {"output": None})
            decision = self.chat.offer(state)["decision"]
            self.assertEqual("confirm", decision["intent"])
            self.assertEqual(state["user_events"][-1]["id"], decision["message_id"])
            self.assertEqual(before.get("goal_contract"), state.get("goal_contract"))
            self.assertEqual({}, state["answers"])
            self.assertFalse(any(e["kind"] == "goal_approval" for e in state["user_events"]))

    def test_stale_malformed_or_duplicate_responses_cannot_change_a_gate(self):
        for factory in (requirements, plan):
            for mutation in ("message", "token", "changed_gate", "empty_reply", "duplicate"):
                state = factory()
                self.chat.begin(state, "yes coplete")
                value = self.response(state)
                if mutation == "message":
                    value["message_id"] = "other"
                elif mutation == "token":
                    value["gate_token"] = "other"
                elif mutation == "changed_gate":
                    state["requirements_handoff"]["report"]["intended_outcome"] = "Different scope"
                elif mutation == "empty_reply":
                    value["assistant_reply"] = ""
                else:
                    self.chat.accept(state, value, {"output": None})
                before = copy.deepcopy(state)
                with self.assertRaises(ValueError):
                    self.chat.accept(state, value, {"output": None})
                self.assertEqual(before, state)

    def test_unsettled_business_questions_and_operational_holds_are_not_approval_gates(self):
        for change in (
            {"pending_questions": [{"id": "Q1", "question": "Which behavior?"}]},
            {"status": "PAUSED_TRANSPORT_CHANGED"},
            {"settings": {}},
        ):
            state = {**requirements(), **change}
            before = copy.deepcopy(state)
            self.assertFalse(self.chat.begin(state, "yes"))
            self.assertEqual(before, state)

    def test_pending_or_uncertain_work_refuses_a_competing_exchange(self):
        for field in ("active_stage", "uncertain_artifacts", "pending_report_repair"):
            state = requirements()
            state[field] = {"pending": True}
            before = copy.deepcopy(state)
            with self.assertRaises(ValueError):
                self.chat.begin(state, "yes")
            self.assertEqual(before, state)
        state = plan()
        self.chat.begin(state, "yes")
        with self.assertRaises(ValueError):
            self.chat.begin(state, "another reply")

    def test_changed_evidence_or_new_input_withdraws_a_saved_interpretation(self):
        for factory in (requirements, plan):
            state = factory()
            self.chat.begin(state, "yes")
            self.chat.accept(state, self.response(state), {"output": None})
            state["user_events"].append({"kind": "brief_feedback", "text": "Wait, change the scope"})
            self.assertIsNone(self.chat.offer(state)["decision"])

    def test_operational_receipts_do_not_hide_a_confirmation_or_its_conversation(self):
        state = requirements()
        self.chat.begin(state, "What starts after this?")
        self.chat.accept(state, self.response(state, "question"), {"output": None})
        self.chat.begin(state, "yes coplete")
        prompt, _ = self.chat.context(state)
        packet = json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])
        self.assertEqual("What starts after this?", packet["prior_exchanges"][-1]["text"])
        self.chat.accept(state, self.response(state), {"output": None})
        state["user_events"].append({"kind": "accounting_receipt", "actor": "core"})
        self.assertEqual("confirm", self.chat.offer(state)["decision"]["intent"])


if __name__ == "__main__":
    unittest.main()
