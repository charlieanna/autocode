"""Task messages stay bound to the anchor while a member owns the live display."""

import copy
import importlib
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import autocode_chat_coordination as coordination
import autocode_chat_messages as messages

from tests.test_chat_tui import screen_ui

ROOT = Path("/project")
RUN = ROOT / ".autocode/runs/task"


def summary():
    return {
        "version": 1,
        "id": "coordinated-task",
        "anchor": str(RUN),
        "conversation": {
            "requests": [],
            "results": [],
            "input": {
                "kind": "coordination_message",
                "next_command": [
                    "autocode",
                    "coordinate",
                    "message",
                    "--run-dir",
                    str(RUN),
                    "--request-id",
                    "REQUEST_ID",
                    "--text",
                    "MESSAGE",
                    "--json",
                ],
            },
        },
    }


def view():
    return {
        "workspace": str(ROOT),
        "run_dir": str(RUN),
        "coordination": {
            "status_command": ["autocode", "coordinate", "status", "--run-dir", str(RUN), "--json"],
        },
    }


def module():
    return importlib.import_module("autocode_chat_task_messages")


class TaskMessageTests(unittest.TestCase):
    def test_anchor_poll_does_not_consume_the_coordinators_labeled_reply(self):
        from tests.test_chat_client import ScriptedChat

        answer = {
            "id": "m1-reply-after",
            "role": "assistant",
            "text": "You approved these plans.",
            "conversation": {"request_id": "m1", "intent": "confirm"},
        }
        packet = summary()
        packet["conversation"]["results"] = [
            {"request_id": "m1", "context": {"kind": "coordinated_plan"}, "reply": answer}
        ]
        for anchor_first in (True, False):
            with self.subTest(anchor_first=anchor_first):
                client = ScriptedChat({})
                self.addCleanup(client.close)
                shown = {**view(), "chat": {"messages": [answer]}}
                if anchor_first:
                    client.show(shown)
                module().show(client, packet)
                client.show(shown)
                module().show(client, packet)
                self.assertEqual(["\nPlan Reviewer: You approved these plans."], client.out)

    def test_standalone_conversation_still_displays_without_a_coordinator(self):
        from tests.test_chat_client import ScriptedChat

        client = ScriptedChat({})
        self.addCleanup(client.close)
        client.show(
            {
                "chat": {
                    "messages": [
                        {"id": "reply", "role": "assistant", "text": "Answer.", "conversation": {"request_id": "m1"}}
                    ]
                }
            }
        )
        self.assertEqual(["\nAutoCode: Answer."], client.out)

    def test_coordinated_approval_reply_is_shown_once_as_plan_reviewer(self):
        client = self.client()
        packet = summary()
        packet["conversation"]["results"] = [
            {
                "request_id": "m1",
                "context": {"kind": "coordinated_plan"},
                "reply": {"text": "You approved these plans."},
            }
        ]
        module().show(client, packet)
        module().show(client, packet)
        client.say.assert_called_once_with("\nPlan Reviewer: You approved these plans.")

    def test_bound_plan_message_keeps_token_out_of_argv_and_requires_matching_receipt(self):
        client = self.client()
        packet = summary()
        packet["approval"] = {"token": "exact-plan"}
        packet["conversation"]["gate"] = {"token": "exact-plan", "decision": None}
        packet["conversation"]["input"]["next_command"][-1:-1] = ["--plan-token", "exact-plan"]

        def respond(argv, **kwargs):
            if argv[1] == "status":
                return SimpleNamespace(returncode=0, stdout=json.dumps(packet))
            self.assertNotIn("exact-plan", argv)
            self.assertEqual("exact-plan", kwargs["env"]["AUTOCODE_COORDINATION_TOKEN"])
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "version": 1,
                        "accepted": True,
                        "consumer": "pending_only",
                        "receipt": {
                            "id": argv[argv.index("--request-id") + 1],
                            "kind": "message",
                            "text": "yes coplete",
                            "coordination_id": packet["id"],
                            "plan_token": "exact-plan",
                        },
                    }
                ),
            )

        client.cli.side_effect = respond
        self.assertEqual("exact-plan", module().Sender(client).send("yes coplete", packet)["plan_token"])

    def test_changed_plan_before_message_submission_is_not_reinterpreted_as_new_approval(self):
        client = self.client()
        packet = summary()
        packet["approval"] = {"token": "earlier"}
        packet["conversation"]["gate"] = {"token": "earlier", "decision": None}
        packet["conversation"]["input"]["next_command"][-1:-1] = ["--plan-token", "earlier"]
        with self.assertRaises(ValueError):
            module().Sender(client).send("yes", packet)
        self.assertEqual(["status"], [call.args[0][1] for call in client.cli.call_args_list])

    def test_every_plan_reply_reaches_the_reviewer_before_approval(self):
        for text in ("yes", "y", "no", "yes coplete", "yes, but keep the old behavior"):
            client = self.client()
            client.said, client.ask, client.advance = set(), Mock(return_value=text), Mock()
            packet = summary()
            packet.update(
                done=False,
                members=[],
                needs={"kind": "approval", "summary": {"runs": []}},
                approval={"token": "exact-plan", "publication": "Local changes only"},
            )
            packet["conversation"]["gate"] = {"token": "exact-plan", "decision": None}
            with patch.object(coordination, "read", return_value=packet), patch.object(module(), "Sender") as sender:
                self.assertIsNone(coordination.step(client, view(), str))
                sender.return_value.send.assert_called_once_with(text, packet)
            client.advance.assert_not_called()

    def test_current_interpretation_takes_only_fresh_coordinated_approval(self):
        client = self.client()
        client.said, client.ask, client.advance = set(), Mock(), Mock()
        packet = summary()
        packet.update(
            done=False,
            members=[],
            needs={"kind": "approval", "summary": {"runs": []}},
            approval={
                "token": "exact-plan",
                "publication": "Local changes only",
                "next_command": [
                    "autocode",
                    "coordinate",
                    "approve",
                    "--run-dir",
                    str(RUN),
                    "--token",
                    "exact-plan",
                    "--json",
                ],
            },
        )
        packet["conversation"]["gate"] = {"token": "exact-plan", "decision": {"intent": "confirm", "message_id": "m1"}}
        with patch.object(coordination, "read", return_value=packet):
            self.assertIsNone(coordination.step(client, view(), str))
        client.ask.assert_not_called()
        client.advance.assert_called_once()
        self.assertEqual("exact-plan", client.advance.call_args.kwargs["env"]["AUTOCODE_COORDINATION_TOKEN"])
        self.assertNotIn("exact-plan", client.advance.call_args.args[0])

    def client(self):
        client = SimpleNamespace(
            workspace=ROOT, run_dir=RUN, status=Mock(return_value=view()), cli=Mock(), seen=set(), say=Mock()
        )

        def respond(argv, **kwargs):
            if argv[1] == "status":
                value = summary()
            else:
                value = {
                    "version": 1,
                    "accepted": True,
                    "consumer": "pending_only",
                    "receipt": {
                        "id": argv[argv.index("--request-id") + 1],
                        "kind": "message",
                        "text": argv[argv.index("--text") + 1],
                        "coordination_id": "coordinated-task",
                    },
                }
            return SimpleNamespace(returncode=0, stdout=json.dumps(value))

        client.cli.side_effect = respond
        return client

    def test_command_fills_only_anchor_named_action_and_rejects_tampering(self):
        packet = summary()
        expected = [
            "coordinate",
            "message",
            "--run-dir",
            str(RUN),
            "--request-id",
            "once",
            "--text",
            "Question?",
            "--json",
        ]
        self.assertEqual(expected, module().command(packet, RUN, "Question?", "once"))
        for change in ("anchor", "command", "kind", "empty"):
            altered = copy.deepcopy(packet)
            if change == "anchor":
                altered["anchor"] = "/other"
            if change == "command":
                altered["conversation"]["input"]["next_command"][2] = "advance"
            if change == "kind":
                altered["conversation"]["input"]["kind"] = "approve"
            with self.subTest(change=change), self.assertRaises(ValueError):
                module().command(altered, RUN, "" if change == "empty" else "Question?", "once")

    def test_sender_rechecks_anchor_offer_and_confirms_pending_only_receipt(self):
        client = self.client()
        receipt = messages.Sender(client).send("Question?", summary())
        self.assertEqual("Question?", receipt["text"])
        self.assertEqual(["status", "message"], [call.args[0][1] for call in client.cli.call_args_list])
        self.assertIn("task-message-" + receipt["id"], client.seen)

    def test_status_and_receipt_use_bounded_coordinator_deadlines(self):
        client = self.client()
        respond = client.cli.side_effect

        def delayed_status(argv, *, timeout=5):
            if argv[1] == "status" and timeout < 10:
                raise coordination.actions.ClientGap("Three-repository status exceeds a single-run read deadline")
            return respond(argv)

        client.cli.side_effect = delayed_status
        receipt = messages.Sender(client).send("Question?", summary())
        self.assertEqual("Question?", receipt["text"])
        self.assertEqual(120, client.cli.call_args_list[0].kwargs.get("timeout"))
        self.assertEqual(30, client.cli.call_args_list[1].kwargs.get("timeout"))

    def test_uncertain_retry_retains_exact_identity_without_targeting_another_chat(self):
        client = self.client()
        sender = messages.Sender(client)
        response = client.cli.side_effect
        sent = []

        def uncertain(argv, **kwargs):
            if argv[1] == "message":
                sent.append(argv)
                raise ValueError("response lost")
            return response(argv, **kwargs)

        client.cli.side_effect = uncertain
        with self.assertRaises(messages.MessageError):
            sender.send("Question?", summary())
        client.cli.side_effect = response
        sender.send("Question?", summary())
        self.assertEqual(sent[0], client.cli.call_args.args[0])
        client.run_dir = ROOT / ".autocode/runs/other"
        count = client.cli.call_count
        with self.assertRaises(messages.MessageError):
            sender.send("Question?", summary())
        self.assertEqual(count, client.cli.call_count)

    def test_mismatched_receipt_keeps_draft(self):
        client = self.client()
        response = client.cli.side_effect

        def wrong(argv, **kwargs):
            reply = response(argv, **kwargs)
            if argv[1] == "message":
                value = json.loads(reply.stdout)
                value["receipt"]["coordination_id"] = "other"
                reply.stdout = json.dumps(value)
            return reply

        client.cli.side_effect = wrong
        with self.assertRaises(messages.MessageError):
            messages.Sender(client).send("Question?", summary())

    def test_read_exposes_task_offer_and_answers_once(self):
        client = self.client()
        packet = summary()
        packet["conversation"]["requests"] = [{"id": "request", "text": "Question?"}]
        packet["conversation"]["results"] = [
            {"request_id": "request", "reply": {"id": "answer", "text": "Assessment."}}
        ]
        client.cli.return_value = SimpleNamespace(returncode=0, stdout=json.dumps(packet))
        client.cli.side_effect = None
        client._observe_conversation = Mock()
        coordination.read(client, view())
        coordination.read(client, view())
        self.assertEqual(2, client._observe_conversation.call_count)
        self.assertEqual(
            ["\nYou: Question?", "\nRequirements: Assessment."], [call.args[0] for call in client.say.call_args_list]
        )

    def test_plan_prompt_accepts_a_question_without_approving_or_advancing(self):
        client = self.client()
        client.said, client.ask, client.advance = set(), Mock(return_value="What about conversion?"), Mock()
        packet = summary()
        packet.update(
            done=False,
            members=[],
            needs={"kind": "approval", "summary": {"runs": []}},
            approval={"token": "exact-plan", "publication": "Local changes only"},
        )
        with patch.object(coordination, "read", return_value=packet):
            result = coordination.step(client, view(), str)
        self.assertIsNone(result)
        self.assertEqual("message", client.cli.call_args.args[0][1])
        client.advance.assert_not_called()

    def test_tui_uses_task_offer_while_member_is_visible_and_clears_it_on_chat_switch(self):
        ui = screen_ui()
        packet = summary()
        ui.observe_conversation(packet)
        ui.observe({"workspace": "/member", "run_dir": "/member/.autocode/runs/m", "needs": {"kind": "continue"}})
        ui.send_message = Mock()
        ui._composer.insert("Could we reuse conversion?")
        self.assertTrue(ui._can_message())
        ui._enter(False)
        ui.send_message.assert_called_once_with("Could we reuse conversion?", packet)
        self.assertEqual("", ui._composer.text)
        ui.switch(ROOT, ROOT / ".autocode/runs/other")
        self.assertFalse(ui._can_message())
