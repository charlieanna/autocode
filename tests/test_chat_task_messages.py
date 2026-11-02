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
