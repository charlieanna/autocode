"""A stopped command leaves the full-screen viewer available for recovery."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import autocode_chat_client as chat
import autocode_client_actions as actions

from tests import ROOT
from tests.terminal_screen import TerminalScreen, require_tmux


class CommandFailureTests(unittest.TestCase):
    def client(self, *, fullscreen=True):
        return chat.ChatClient(
            Path("/tmp/failure-chat").resolve(),
            Path("/tmp/failure-chat/run").resolve(),
            observe=Mock() if fullscreen else None,
            unavailable=Mock() if fullscreen else None,
            ask=Mock(side_effect=EOFError),
            say=Mock(),
        )

    def test_fullscreen_command_failure_waits_for_input_without_retrying(self):
        for detached in (False, True):
            with self.subTest(detached=detached):
                client = self.client()
                if detached:
                    client._observe = client._unavailable = None
                with patch.object(client, "step", side_effect=actions.ClientGap("Worker stopped")) as step:
                    self.assertEqual(2, client.converse())
                step.assert_called_once()
                self.assertIn("/quit", client._ask.call_args.args[0])
                self.assertIn("Worker stopped", " ".join(call.args[0] for call in client._say.call_args_list))

    def test_only_enter_rechecks_after_a_failure_and_another_failure_waits_again(self):
        client = self.client()
        client._ask.side_effect = ["yes", "", EOFError]
        with patch.object(client, "step", side_effect=actions.ClientGap("Worker stopped")) as step:
            self.assertEqual(2, client.converse())
        self.assertEqual(2, step.call_count)
        self.assertEqual(3, client._ask.call_count)
        self.assertIn("Nothing sent", " ".join(call.args[0] for call in client._say.call_args_list))

    def test_switching_chats_leaves_the_failed_command_without_replaying_it(self):
        client = self.client()
        client._ask.side_effect = chat.Switched
        with patch.object(client, "step", side_effect=[actions.ClientGap("Worker stopped"), 0]) as step:
            self.assertEqual(0, client.converse())
        self.assertEqual(2, step.call_count)
        client._ask.assert_called_once()

    def test_ctrl_c_at_the_failure_prompt_explicitly_leaves_without_retrying(self):
        client = self.client()
        client._ask.side_effect = [KeyboardInterrupt, EOFError]
        with patch.object(client, "step", side_effect=actions.ClientGap("Worker stopped")) as step:
            self.assertEqual(2, client.converse())
        step.assert_called_once()
        client._ask.assert_called_once()

    def test_line_client_still_reports_failure_and_exits(self):
        client = self.client(fullscreen=False)
        with patch.object(client, "step", side_effect=actions.ClientGap("Worker stopped")):
            self.assertEqual(1, client.converse())
        client._ask.assert_not_called()


class CommandFailureTerminalTests(unittest.TestCase):
    def test_failed_public_command_keeps_error_composer_navigation_and_saved_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            backend = root / "backend.py"
            backend.write_text(f"""
import json, sys
from pathlib import Path
root = Path({str(root)!r})
args = sys.argv[1:]
with (root / 'calls.jsonl').open('a') as output:
    output.write(json.dumps(args) + '\\n')
if '--status' in args:
    failed = (root / 'failed').exists()
    print(json.dumps({{'view': {{'workspace': str(root), 'run_dir': str(root / 'run'),
        'status': 'PAUSED_PROCESS_CLEANUP' if failed else 'RUNNING',
        'needs': {{'kind': 'resume' if failed else 'continue'}},
        'next_command': 'autocode --resume-paused' if failed else 'autocode --no-chat',
        'chat': {{'log': []}}}}}}))
elif '--no-chat' in args:
    (root / 'failed').touch()
    print('Worker stopped without verified cleanup')
    raise SystemExit(1)
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
    client = ChatClient(root, root / 'run', command=(sys.executable, {str(backend)!r}),
        observe=ui.observe, unavailable=ui.status_unavailable, say=ui.say, ask=ui.ask,
        wait=ui.wait, poll_seconds=0.1)
    ui.bind(client.slash, run=True)
    ui.idle = client.poll
    code = client.converse()
raise SystemExit(code)
"""
            with TerminalScreen(
                [sys.executable, "-c", script], cwd=ROOT, env=os.environ, cols=120, rows=30, tmux=require_tmux(self)
            ) as screen:
                screen.wait_for(
                    lambda text: all(s in text for s in ("Worker stopped without verified cleanup", "/quit", "╰─")),
                    timeout=10,
                )
                screen.type("/help")
                screen.keys("Enter")
                screen.wait_for(lambda text: "List these commands" in text and "╰─" in text, timeout=5)
                screen.keys("Enter")
                screen.wait_for(lambda text: "Continue?" in text and "╰─" in text, timeout=5)
                screen.type("/quit")
                screen.keys("Enter")
                self.assertEqual(2, screen.exit_status())
            calls = [json.loads(line) for line in (root / "calls.jsonl").read_text().splitlines()]
            self.assertEqual(1, sum("--no-chat" in call for call in calls))
            self.assertFalse(any("--resume-paused" in call for call in calls))
