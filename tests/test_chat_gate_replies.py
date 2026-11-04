"""Typed gate replies reach an agent before a client takes an exact core action."""

import copy
import unittest

import autocode_client_actions as actions

from tests import test_chat_client as fixtures

DONE, PLACE, RUN, WORKSPACE = fixtures.DONE, fixtures.PLACE, fixtures.RUN, fixtures.WORKSPACE
ScriptedChat = fixtures.ScriptedChat


def understanding(decision=None):
    value = copy.deepcopy(fixtures.ConversationTests.REVIEW)
    value["chat"]["gate"] = {
        "kind": "requirements",
        "token": "RQ",
        "stage": "requirements_gather",
        "decision": decision,
    }
    return value


def reviewed(decision=None):
    return {
        "status": "AWAITING_GOAL_APPROVAL",
        "needs": {"kind": "approve_plan", "token": "t"},
        "displayed_plan": {"token": "t", "acceptance_criteria": [], "constraints": [], "permission_boundaries": []},
        "next_command": f"AUTOCODE_PLAN_TOKEN=t autocode {PLACE} --approve-plan --plan-token -",
        "chat": {
            "context": "plan-context",
            "gate": {"kind": "plan", "token": "t", "stage": "astra_finalize", "decision": decision},
            "input": {"kind": "feedback", "next_command": f"autocode {PLACE} --feedback MESSAGE"},
        },
    }


class ConversationalGateClientTests(unittest.TestCase):
    def test_every_nonempty_requirements_reply_is_sent_for_interpretation(self):
        for text in ("y", "yes", "no", "yes coplete", "yes, but include existing entries"):
            view = understanding()
            client = ScriptedChat(view, DONE, replies=[text])
            self.addCleanup(client.close)
            self.assertTrue(client.requirements(view, actions.named(view, WORKSPACE, RUN)))
            self.assertEqual([["--feedback", text]], client.taken)
            self.assertIn("Planning uses your selected models.", client.text())
            self.assertNotIn("[y/N]", client.text())

    def test_every_nonempty_plan_reply_is_sent_to_its_agent_before_approval(self):
        for text in ("y", "yes", "no", "yes coplete", "yes, but remove a constraint"):
            view = reviewed()
            client = ScriptedChat(view, DONE, replies=[text])
            self.addCleanup(client.close)
            self.assertTrue(client.approve(view))
            self.assertEqual([["--feedback", text]], client.taken)

    def test_interpreted_confirmation_takes_only_the_fresh_named_gate_action(self):
        for factory, expected in (
            (understanding, ["--answer", "RQ=plan"]),
            (reviewed, ["--approve-plan", "--plan-token", "-"]),
        ):
            view = factory({"message_id": "message-1", "intent": "confirm"})
            client = ScriptedChat(view, DONE)
            self.addCleanup(client.close)
            self.assertTrue(client.respond(view))
            self.assertEqual([expected], client.taken)

    def test_changed_context_withdraws_an_interpreted_confirmation(self):
        for factory in (understanding, reviewed):
            view = factory({"message_id": "message-1", "intent": "confirm"})
            fresh = copy.deepcopy(view)
            fresh["chat"]["context"] = "changed"
            client = ScriptedChat(fresh)
            self.addCleanup(client.close)
            self.assertTrue(client.respond(view))
            self.assertEqual([], client.taken)

    def test_requirements_interpretation_cannot_answer_a_different_question(self):
        view = understanding({"message_id": "message-1", "intent": "confirm"})
        view["chat"]["gate"]["token"] = "earlier-question"
        client = ScriptedChat(view, DONE)
        self.addCleanup(client.close)
        with self.assertRaisesRegex(actions.ClientGap, "understanding"):
            client.respond(view)
        self.assertEqual([], client.taken)


if __name__ == "__main__":
    unittest.main()
