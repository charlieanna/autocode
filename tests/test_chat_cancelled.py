"""A saved coordination stop leaves the full-screen viewer usable, without resuming."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import autocode_chat_coordination as coordination

from tests import ROOT
from tests.terminal_screen import TerminalScreen, require_tmux


class CancelledChatTests(unittest.TestCase):
    def setUp(self):
        self.summary = {
            "done": False,
            "members": [],
            "needs": {"kind": "cancelled", "reason": "Work stopped; saved work is retained."},
        }
        self.client = SimpleNamespace(
            _observe=Mock(), said=set(), ask=Mock(return_value=""), say=Mock(), advance=Mock()
        )

    def test_cancelled_fullscreen_waits_for_input_and_does_not_resume_or_repeat_notice(self):
        with patch.object(coordination, "read", return_value=self.summary):
            for _ in range(2):
                self.assertIsNone(coordination.step(self.client, {}, str))
        self.assertEqual(2, self.client.ask.call_count)
        self.assertIn("/quit", self.client.ask.call_args.args[0])
        self.client.say.assert_called_once_with(self.summary["needs"]["reason"])
        self.client.advance.assert_not_called()

    def test_line_client_keeps_exit_two_without_prompt(self):
        self.client._observe = None
        with patch.object(coordination, "read", return_value=self.summary):
            self.assertEqual(2, coordination.step(self.client, {}, str))
        self.client.ask.assert_not_called()
        self.client.advance.assert_not_called()

    def test_text_without_a_public_message_offer_is_not_submitted(self):
        self.client.ask.return_value = "continue"
        with (
            patch.object(coordination, "read", return_value=self.summary),
            patch.object(coordination.task_messages, "Sender") as sender,
        ):
            self.assertIsNone(coordination.step(self.client, {}, str))
        sender.assert_not_called()
        self.client.advance.assert_not_called()
        self.assertIn("Nothing sent", self.client.say.call_args.args[0])

    def test_offered_message_uses_existing_sender_and_preserves_uncertain_receipt(self):
        self.summary["conversation"] = {"input": {"kind": "coordination_message"}}
        self.client.ask.return_value = "Keep the original scope"
        with (
            patch.object(coordination, "read", return_value=self.summary),
            patch.object(coordination.task_messages, "Sender") as sender,
        ):
            sender.return_value.send.side_effect = [ValueError("unconfirmed"), None]
            self.assertIsNone(coordination.step(self.client, {}, str))
            self.assertIn("receipt is unconfirmed", self.client.say.call_args.args[0])
            self.assertIsNone(coordination.step(self.client, {}, str))
        sender.assert_called_once_with(self.client)
        self.assertEqual(2, sender.return_value.send.call_count)
        sender.return_value.send.assert_called_with("Keep the original scope", self.summary)
        self.client.advance.assert_not_called()

    def test_explicit_viewer_exit_propagates(self):
        self.client.ask.side_effect = EOFError
        with patch.object(coordination, "read", return_value=self.summary), self.assertRaises(EOFError):
            coordination.step(self.client, {}, str)
        self.client.advance.assert_not_called()


class CancelledTerminalTests(unittest.TestCase):
    def test_public_cancelled_view_keeps_composer_and_commands_until_explicit_quit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            backend = root / "backend.py"
            backend.write_text("""
import json, sys
from pathlib import Path
root = Path(sys.argv[1])
args = sys.argv[2:]
anchor = str(root / 'anchor')
with (root / 'calls.jsonl').open('a') as out:
    out.write(json.dumps(args) + '\\n')
if '--status' in args:
    print(json.dumps({'view': {'workspace': str(root), 'run_dir': anchor,
        'status': 'WAITING_FOR_USER', 'needs': {'kind': 'answer'}, 'chat': {'log': []},
        'coordination': {'status_command': ['autocode', 'coordinate', 'status', '--run-dir', anchor, '--json']}}}))
elif args[:2] == ['coordinate', 'status']:
    print(json.dumps({'version': 1, 'anchor': anchor, 'done': False, 'members': [],
        'needs': {'kind': 'cancelled', 'reason': 'Work stopped; saved work is retained.'}}))
else:
    raise SystemExit('Unexpected action: ' + repr(args))
""")
            script = f"""
import sys
from pathlib import Path
sys.path.insert(0, 'tools')
from autocode_chat_client import ChatClient
from autocode_chat_tui import ChatTUI
root = Path({str(root)!r})
with ChatTUI() as ui:
    client = ChatClient(root, root / 'anchor', command=(sys.executable, {str(backend)!r}, str(root)),
        observe=ui.observe, say=ui.say, ask=ui.ask, wait=ui.wait, poll_seconds=0.1)
    ui.bind(client.slash, run=True)
    ui.idle = client.poll
    code = client.converse()
raise SystemExit(code)
"""
            with TerminalScreen(
                [sys.executable, "-c", script],
                cwd=ROOT,
                env=os.environ,
                cols=110,
                rows=30,
                tmux=require_tmux(self),
            ) as screen:
                screen.wait_for(
                    lambda shown: all(text in shown for text in ("saved work is retained", "/quit", "╰─")),
                    timeout=5,
                    what="saved stop with an open composer instead of an exited viewer",
                )
                screen.type("/help")
                screen.keys("Enter")
                screen.wait_for(
                    lambda shown: "List these commands" in shown and "╰─" in shown,
                    what="commands still work after cancellation",
                )
                screen.type("/quit")
                screen.keys("Enter")
                self.assertEqual(2, screen.exit_status())
            calls = [json.loads(line) for line in (root / "calls.jsonl").read_text().splitlines()]
            self.assertTrue(any(call[:2] == ["coordinate", "status"] for call in calls))
            self.assertTrue(all("--status" in call or call[:2] == ["coordinate", "status"] for call in calls))
