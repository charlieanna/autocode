"""Real terminal sends a task message while a real fake-provider Planner owns work."""

import os
import sys
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_coordination_budget as budget
import autocode_coordination_store as store
import autocode_taskrun as taskrun

from tests import ROOT
from tests.terminal_screen import TerminalScreen, require_tmux
from tests.test_agent_step import fixture_workspace
from tests.test_taskrun import BRIEF, FIXTURE_OPTIONS


@unittest.skipUnless(sys.platform == "linux", "supervised provider requires supported Linux")
class TaskMessageScreenTests(unittest.TestCase):
    def test_message_while_member_runs_is_answered_at_anchor_once(self):
        workspace, env = fixture_workspace(self)
        peer_workspace, _ = fixture_workspace(self)
        self.enterContext(patch.dict(os.environ, env))
        provider = Path(env["PATH"].split(os.pathsep)[0]) / "codex"
        gate, ready, calls = (provider.parent / name for name in ("planner-gate", "planner-ready", "planner-calls"))
        os.mkfifo(gate)
        marker = '    output = Path(sys.argv[sys.argv.index("-o") + 1])'
        amendment = textwrap.dedent(f"""
            with Path({str(calls)!r}).open('a') as log:
                log.write(stage + (' conversation' if data.get('message') else '') + '\\n')
            if stage == 'astra_discovery' and not Path({str(ready)!r}).exists():
                Path({str(ready)!r}).write_text('ready')
                with Path({str(gate)!r}).open() as signal:
                    signal.read()
            if stage == 'requirements_gather' and data.get('message'):
                message = data['message']
                report = {{'message_id': message['id'], 'contract_token': message['contract_token'],
                           'intent': 'question', 'assistant_reply': 'We should assess conversion fidelity.', 'affected_targets': []}}
        """)
        self.assertIn(marker, provider.read_text())
        provider.write_text(provider.read_text().replace(marker, textwrap.indent(amendment, "    ") + "\n" + marker))
        source = taskrun.TaskRun.start(
            workspace, BRIEF, options=(*FIXTURE_OPTIONS, "--spec-first", "--no-adaptive-planning"), env=env, timeout=240
        )
        source_before = source.advance_until_input()
        peer = taskrun.TaskRun.start(
            peer_workspace,
            BRIEF,
            options=(*FIXTURE_OPTIONS, "--spec-first", "--no-adaptive-planning"),
            env=env,
            timeout=240,
        )
        gathered = peer.advance_until_input()
        question = gathered["needs"]["questions"][-1]
        peer.answer(question["id"], "plan", resolver_token=gathered["needs"]["resolver_token"])
        store.save(
            {
                "version": 1,
                "id": "live-question-fixture",
                "anchor": str(source.run_dir),
                "phase": "fixing",
                "task": BRIEF,
                "request_sources": [],
                "choices": {},
                "initial_scope": [],
                "joint_attempts": [],
                "members": [
                    {
                        "target": "peer",
                        "workspace": str(peer.workspace),
                        "run_dir": str(peer.run_dir),
                        "base": "fixture",
                        "started": True,
                        "brief": BRIEF,
                    }
                ],
                "budget": {**budget.initial(source_before), "cap_usd": 0},
            }
        )

        script = f"""
import sys
from pathlib import Path
sys.path.insert(0, {str(ROOT / "tools")!r})
from autocode_chat_client import ChatClient
from autocode_chat_tui import ChatTUI
from autocode_chat_messages import Sender
import autocode_chat_coordination as coordination
with ChatTUI() as ui:
    client = ChatClient(Path({str(source.workspace)!r}), Path({str(source.run_dir)!r}),
        say=ui.say, ask=ui.ask, observe=ui.observe, observe_conversation=ui.observe_conversation,
        observe_coordination=ui.observe_coordination, unavailable=ui.status_unavailable,
        wait=ui.wait, poll_seconds=0.1)
    ui.bind(client.slash)
    ui.send_message = Sender(client).send
    ui.idle = client.poll
    try:
        for _ in range(30):
            current = client.status()
            summary = coordination.read(client, current)
            if ((summary.get('conversation') or {{}}).get('results')
                    and summary['members'][0]['status'] == 'AWAITING_GOAL_APPROVAL'):
                ui.ask('Question answered; plan still awaits approval. Close this test viewer?')
                break
            coordination.step(client, current, str)
        else:
            raise AssertionError('Conversation did not settle')
    except EOFError:
        pass
    finally:
        client.close()
"""
        with TerminalScreen(
            [sys.executable, "-c", script],
            cwd=workspace,
            env={**os.environ, **env},
            cols=145,
            rows=100,
            tmux=require_tmux(self),
        ) as screen:
            try:
                screen.wait_for(
                    lambda shown: ready.exists() and "Type a message" in shown,
                    60,
                    what="member Planner with anchor-owned composer",
                )
                screen.paste("Could we reuse the conversion?")
                screen.keys("Enter")
                screen.wait_for(lambda shown: "Message saved" in shown and "Requirements will reply" in shown, 30)
            finally:
                if ready.exists():
                    try:
                        descriptor = os.open(gate, os.O_WRONLY | os.O_NONBLOCK)
                    except OSError:
                        pass
                    else:
                        with os.fdopen(descriptor, "w") as release:
                            release.write("continue")
            screen.wait_for(
                lambda shown: (
                    "We should assess conversion fidelity." in shown
                    and "Question answered; plan still awaits approval." in shown
                ),
                300,
                what="one Requirements reply and resumed original plan",
            )
            screen.keys("C-d")
            self.assertEqual(0, screen.exit_status(15))
        events = calls.read_text().splitlines()
        self.assertEqual(1, events.count("astra_discovery"), events)
        self.assertEqual(1, events.count("requirements_gather conversation"), events)
        self.assertNotIn("terra", events)
        self.assertEqual("approve_plan", peer.status()["needs"]["kind"])
