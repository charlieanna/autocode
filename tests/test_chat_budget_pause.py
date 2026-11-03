"""Declining cost recovery keeps a full-screen chat usable without changing the run."""

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


def offer(run, kind):
    operation = {"budget": "budget", "planning_budget": "planning-budget", "coordination_resume": "resume"}[kind]
    flag = "--max-planning-cost-usd" if kind == "planning_budget" else "--max-cost-usd"
    return {
        "kind": kind,
        "cap_usd": 10,
        "known_spent_usd": 9,
        "complete": True,
        "minimum_cap_usd": 11,
        "maximum_cap_usd": 20,
        "planning_cap_usd": 8,
        "planning_spent_usd": 8,
        "token": "recovery-token",
        "effect": "Resume saved work without approving a plan.",
        "next_command": [
            "autocode",
            "coordinate",
            operation,
            "--run-dir",
            str(run),
            "--token",
            "recovery-token",
            flag,
            "N",
            "--json",
        ],
    }


class PausedBudgetTests(unittest.TestCase):
    def test_fullscreen_decline_waits_without_dispatch_for_each_recovery_kind(self):
        for kind in ("budget", "planning_budget", "coordination_resume"):
            for answers in (["", ""], ["12", "no", ""]):
                with self.subTest(kind=kind, answers=answers):
                    client = SimpleNamespace(
                        run_dir=Path("/tmp/budget-chat").resolve(),
                        _observe=Mock(),
                        ask=Mock(side_effect=answers),
                        say=Mock(),
                        advance=Mock(),
                    )
                    need = offer(client.run_dir, kind)
                    with patch.object(coordination, "read") as read:
                        self.assertIsNone(coordination.budget_reply(client, {}, need))
                    self.assertIn("/quit", client.ask.call_args.args[0])
                    self.assertIn(
                        "No spending limit changed", " ".join(call.args[0] for call in client.say.call_args_list)
                    )
                    client.advance.assert_not_called()
                    read.assert_not_called()

    def test_paused_budget_line_client_exits_without_another_prompt(self):
        client = SimpleNamespace(_observe=None, say=Mock(), ask=Mock())
        self.assertEqual(2, coordination.paused_budget_reply(client))
        client.ask.assert_not_called()

    def test_paused_budget_free_text_grants_no_action_and_explicit_exit_propagates(self):
        client = SimpleNamespace(_observe=Mock(), say=Mock(), ask=Mock(return_value="yes"), advance=Mock())
        self.assertIsNone(coordination.paused_budget_reply(client))
        self.assertIn("Nothing sent", client.say.call_args.args[0])
        client.advance.assert_not_called()
        client.ask.side_effect = EOFError
        with self.assertRaises(EOFError):
            coordination.paused_budget_reply(client)


class PausedBudgetTerminalTests(unittest.TestCase):
    def test_blank_recovery_keeps_composer_help_and_explicit_quit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            run = root / "anchor"
            backend = root / "backend.py"
            need = offer(run, "coordination_resume")
            backend.write_text(f"""
import json, sys
from pathlib import Path
root = Path({str(root)!r})
anchor = {str(run)!r}
args = sys.argv[1:]
with (root / 'calls.jsonl').open('a') as out:
    out.write(json.dumps(args) + '\\n')
if '--status' in args:
    print(json.dumps({{'view': {{'workspace': str(root), 'run_dir': anchor,
        'status': 'WAITING_FOR_USER', 'needs': {{'kind': 'answer'}}, 'chat': {{'log': []}},
        'coordination': {{'status_command': ['autocode', 'coordinate', 'status', '--run-dir', anchor, '--json']}}}}}}))
elif args[:2] == ['coordinate', 'status']:
    print(json.dumps({{'version': 1, 'anchor': anchor, 'done': False, 'members': [], 'needs': {need!r}}}))
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
    client = ChatClient(root, root / 'anchor', command=(sys.executable, {str(backend)!r}),
        observe=ui.observe, say=ui.say, ask=ui.ask, wait=ui.wait, poll_seconds=0.1)
    ui.bind(client.slash, run=True)
    ui.idle = client.poll
    code = client.converse()
raise SystemExit(code)
"""
            with TerminalScreen(
                [sys.executable, "-c", script], cwd=ROOT, env=os.environ, cols=120, rows=30, tmux=require_tmux(self)
            ) as screen:
                screen.wait_for("Enter keeps it paused", timeout=5)
                screen.keys("Enter")
                screen.wait_for(
                    lambda text: all(s in text for s in ("No spending limit changed", "/quit", "╰─")), timeout=5
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
