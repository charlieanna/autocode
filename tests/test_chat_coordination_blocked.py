"""A blocked coordinator keeps the terminal available without retrying work."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import autocode_chat_client as chat
import autocode_chat_coordination as coordination

from tests import ROOT
from tests.terminal_screen import TerminalScreen, require_tmux


class BlockedCoordinationTests(unittest.TestCase):
    def test_fullscreen_block_waits_and_rechecks_without_replaying_an_action(self):
        root = Path("/tmp/blocked-coordination").resolve()
        client = chat.ChatClient(root, root / "run", observe=Mock(), say=Mock(), ask=Mock(return_value=""))
        blocked = {
            "done": False,
            "members": [],
            "needs": {"kind": "blocked", "reason": "Source catalog is unavailable"},
            "next_command": None,
        }
        ready = {
            **blocked,
            "needs": {"kind": "start"},
            "next_command": ["autocode", "coordinate", "advance", "--run-dir", str(root / "run"), "--json"],
        }
        with (
            patch.object(coordination, "read", side_effect=[blocked, ready]),
            patch.object(coordination, "advance_with_progress") as advance,
        ):
            self.assertIsNone(coordination.step(client, {}, str))
            advance.assert_not_called()
            client._ask.assert_called_once()
            self.assertIn("/quit", client._ask.call_args.args[0])
            self.assertIsNone(coordination.step(client, {}, str))
            advance.assert_called_once_with(client, ready, ready["next_command"][1:])

    def test_blocked_line_client_reports_reason_and_exits(self):
        root = Path("/tmp/blocked-coordination").resolve()
        client = chat.ChatClient(root, root / "run", say=Mock(), ask=Mock())
        summary = {
            "done": False,
            "members": [],
            "needs": {"kind": "blocked", "reason": "Source catalog is unavailable"},
            "next_command": None,
        }
        with patch.object(coordination, "read", return_value=summary):
            self.assertEqual(2, coordination.step(client, {}, str))
        client._ask.assert_not_called()
        client._say.assert_called_once_with("Source catalog is unavailable")

    def test_text_without_public_message_offer_does_not_authorize_work(self):
        root = Path("/tmp/blocked-coordination").resolve()
        client = chat.ChatClient(root, root / "run", observe=Mock(), say=Mock(), ask=Mock(return_value="yes"))
        summary = {
            "done": False,
            "members": [],
            "needs": {"kind": "blocked", "reason": "Source catalog is unavailable"},
            "next_command": None,
        }
        with patch.object(coordination, "read", return_value=summary), patch.object(client, "advance") as advance:
            self.assertIsNone(coordination.step(client, {}, str))
        advance.assert_not_called()
        self.assertIn("Nothing sent", client._say.call_args.args[0])


class BlockedCoordinationTerminalTests(unittest.TestCase):
    def test_public_blocked_status_retains_reason_composer_and_navigation_until_quit(self):
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
    print(json.dumps({{'view': {{'workspace': str(root), 'run_dir': str(root / 'run'),
        'status': 'WAITING_FOR_USER', 'needs': {{'kind': 'answer'}},
        'coordination': {{'status_command': ['autocode', 'coordinate', 'status',
            '--run-dir', str(root / 'run'), '--json']}}, 'chat': {{'log': []}}}}}}))
elif args[:2] == ['coordinate', 'status']:
    print(json.dumps({{'version': 1, 'anchor': str(root / 'run'), 'done': False, 'members': [],
        'needs': {{'kind': 'blocked', 'reason': 'Source catalog is unavailable: repository_unreadable'}},
        'next_command': None}}))
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
                    lambda text: all(s in text for s in ("Source catalog is unavailable", "/quit", "╰─")),
                    timeout=10,
                )
                screen.type("/help")
                screen.keys("Enter")
                screen.wait_for(lambda text: "List these commands" in text and "╰─" in text, timeout=5)
                screen.type("/quit")
                screen.keys("Enter")
                self.assertEqual(2, screen.exit_status())
            calls = [json.loads(line) for line in (root / "calls.jsonl").read_text().splitlines()]
            self.assertTrue(any(call[:2] == ["coordinate", "status"] for call in calls))
            self.assertTrue(all("--status" in call or call[:2] == ["coordinate", "status"] for call in calls))
