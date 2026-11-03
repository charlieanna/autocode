"""Typed replies after command failure use only an offered pending task message."""

import importlib
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import autocode_chat_client as chat
import autocode_chat_coordination as coordination
import autocode_chat_task_messages as task_messages


class FailedInputTests(unittest.TestCase):
    def setUp(self):
        self.workspace = Path("/tmp/failed-input-project")
        self.run = self.workspace / ".autocode/runs/task"
        self.view = {
            "workspace": str(self.workspace),
            "run_dir": str(self.run),
            "coordination": {
                "status_command": ["autocode", "coordinate", "status", "--run-dir", str(self.run), "--json"]
            },
        }
        self.summary = {
            "version": 1,
            "anchor": str(self.run),
            "id": "task-set",
            "conversation": {"input": {"kind": "coordination_message"}},
        }
        self.client = SimpleNamespace(
            workspace=self.workspace,
            run_dir=self.run,
            ask=Mock(return_value="Which domain?"),
            status=Mock(return_value=self.view),
            say=Mock(),
            advance=Mock(),
            take=Mock(),
            _task_message_sender=None,
        )

    def reply(self):
        return importlib.import_module("autocode_chat_failed_input").reply(self.client)

    def test_pending_message_is_sent_without_retrying_or_approving_the_failed_action(self):
        with (
            patch.object(coordination, "read", return_value=self.summary),
            patch.object(task_messages, "Sender") as sender,
        ):
            self.assertTrue(self.reply())
        sender.assert_called_once_with(self.client)
        sender.return_value.send.assert_called_once_with("Which domain?", self.summary)
        self.client.advance.assert_not_called()
        self.client.take.assert_not_called()

    def test_blank_explicitly_rechecks_status_without_submitting_a_message(self):
        self.client.ask.return_value = ""
        with patch.object(task_messages, "Sender") as sender:
            self.assertTrue(self.reply())
        self.client.status.assert_not_called()
        sender.assert_not_called()

    def test_absent_or_unavailable_offer_never_sends_or_retries(self):
        for unavailable in (False, True):
            with self.subTest(unavailable=unavailable):
                self.client.status.side_effect = chat.actions.ClientGap("offline") if unavailable else None
                with (
                    patch.object(coordination, "read", return_value={**self.summary, "conversation": {}}),
                    patch.object(task_messages, "Sender") as sender,
                ):
                    self.assertFalse(self.reply())
                sender.assert_not_called()
                self.client.advance.assert_not_called()

    def test_uncertain_receipt_keeps_the_existing_sender_for_the_same_exact_retry(self):
        retained = Mock()
        retained.send.side_effect = [ValueError("lost receipt"), {"id": "same-request"}]
        self.client._task_message_sender = retained
        with (
            patch.object(coordination, "read", return_value=self.summary),
            patch.object(task_messages, "Sender") as sender,
        ):
            self.assertFalse(self.reply())
            self.assertTrue(self.reply())
        sender.assert_not_called()
        self.assertEqual(
            [(("Which domain?", self.summary), {}), (("Which domain?", self.summary), {})],
            [(c.args, c.kwargs) for c in retained.send.call_args_list],
        )
        self.assertTrue(any("unconfirmed" in c.args[0] for c in self.client.say.call_args_list))

    def test_conversation_accepts_a_reply_at_its_error_prompt_then_reads_the_run_again(self):
        lines = []
        client = chat.ChatClient(
            self.workspace,
            self.run,
            observe=Mock(),
            unavailable=Mock(),
            say=lines.append,
            ask=Mock(side_effect=["Which domain?", EOFError()]),
        )
        client.status = Mock(return_value=self.view)
        client.step = Mock(side_effect=[chat.actions.ClientGap("Coordination stopped"), 0])
        with (
            patch.object(coordination, "read", return_value=self.summary),
            patch.object(task_messages, "Sender") as sender,
        ):
            self.assertEqual(0, client.converse())
        sender.return_value.send.assert_called_once_with("Which domain?", self.summary)
        self.assertEqual(2, client.step.call_count)
        self.assertFalse(any("Nothing sent" in line for line in lines))


class FailedInputTerminalTests(unittest.TestCase):
    def test_actual_error_composer_submits_one_public_message_without_replaying_failure(self):
        import json
        import os
        import sys
        import tempfile

        from tests import ROOT
        from tests.terminal_screen import TerminalScreen, require_tmux

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            backend = root / "backend.py"
            backend.write_text("""
import json, sys
from pathlib import Path
root = Path(sys.argv[1]); args = sys.argv[2:]; anchor = str(root / 'anchor')
with (root / 'calls.jsonl').open('a') as log: log.write(json.dumps(args) + '\\n')
offer = ['autocode', 'coordinate', 'status', '--run-dir', anchor, '--json']
message = ['autocode', 'coordinate', 'message', '--run-dir', anchor, '--request-id', 'REQUEST_ID', '--text', 'MESSAGE', '--json']
if '--status' in args:
    print(json.dumps({'view': {'workspace': str(root), 'run_dir': anchor, 'status': 'WAITING_FOR_USER',
        'needs': {'kind': 'answer'}, 'chat': {'log': []}, 'coordination': {'status_command': offer}}}))
elif args[:2] == ['coordinate', 'status']:
    accepted = (root / 'accepted.json').exists()
    print(json.dumps({'version': 1, 'anchor': anchor, 'id': 'failed-task', 'members': [], 'done': False,
        'needs': {'kind': 'blocked', 'reason': 'Message accepted; task safely held.'} if accepted else {'kind': 'advance'},
        'next_command': None if accepted else ['autocode', 'coordinate', 'advance', '--run-dir', anchor, '--json'],
        'conversation': {'requests': [], 'results': [], 'input': {'kind': 'coordination_message', 'next_command': message}}}))
elif args[:2] == ['coordinate', 'advance']:
    print(json.dumps({'error': 'Fixture assignment refused'})); raise SystemExit(2)
elif args[:2] == ['coordinate', 'message']:
    receipt = {'id': args[args.index('--request-id') + 1], 'kind': 'message',
               'text': args[args.index('--text') + 1], 'coordination_id': 'failed-task'}
    (root / 'accepted.json').write_text(json.dumps(receipt))
    print(json.dumps({'version': 1, 'accepted': True, 'consumer': 'pending_only', 'receipt': receipt}))
else: raise SystemExit('Unexpected command: ' + repr(args))
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
        observe=ui.observe, unavailable=ui.status_unavailable, say=ui.say, ask=ui.ask, wait=ui.wait, poll_seconds=0.1)
    ui.bind(client.slash, run=True)
    ui.idle = client.poll
    code = client.converse()
raise SystemExit(code)
"""
            with TerminalScreen(
                [sys.executable, "-c", script], cwd=ROOT, env=os.environ, cols=115, rows=32, tmux=require_tmux(self)
            ) as screen:
                screen.wait_for(
                    lambda text: "Coordination stopped" in text and "╰─" in text,
                    timeout=10,
                    what="failed action with a usable composer",
                )
                screen.type("Which domain example is needed?")
                screen.keys("Enter")
                screen.wait_for(
                    lambda text: "Message accepted; task safely held." in text and "╰─" in text,
                    timeout=10,
                    what="confirmed pending-only message after failure",
                )
                receipt = json.loads((root / "accepted.json").read_text())
                self.assertEqual("Which domain example is needed?", receipt["text"])
                calls = [json.loads(line) for line in (root / "calls.jsonl").read_text().splitlines()]
                self.assertEqual(1, sum(c[:2] == ["coordinate", "advance"] for c in calls))
                self.assertEqual(1, sum(c[:2] == ["coordinate", "message"] for c in calls))
                self.assertFalse(any(c[:2] == ["coordinate", "approve"] for c in calls))
                screen.type("/quit")
                screen.keys("Enter")
                self.assertEqual(2, screen.exit_status())
