"""Real terminal: a typed mid-Planner question gets a reply without another draft."""

import os
import sys
import textwrap
import unittest
from pathlib import Path

import autocode_taskrun as taskrun

from tests import ROOT
from tests.terminal_screen import TerminalScreen, require_tmux
from tests.test_agent_step import fixture_workspace
from tests.test_run_messages_cli import QUESTION, REPLY
from tests.test_taskrun import BRIEF, FIXTURE_OPTIONS


@unittest.skipUnless(sys.platform == "linux", "supervised provider requires supported Linux")
class LiveComposerScreenTests(unittest.TestCase):
    def test_requirements_reply_appears_once_before_and_after_reply_and_reopen(self):
        workspace, env = fixture_workspace(self)
        env["PATH"] = os.pathsep.join([str(Path(sys.executable).parent), env["PATH"]])
        run = taskrun.TaskRun.start(
            workspace, "Hi", options=(*FIXTURE_OPTIONS, "--spec-first", "--no-adaptive-planning"), env=env, timeout=300
        )
        gathered = run.advance_until_input()
        # "Hi" is answered like any chat: the reply alone, never the runner's canned question under it.
        canned = gathered["needs"]["questions"][0]["question"]
        question = [row for row in gathered["chat"]["messages"] if row["role"] == "assistant"][-1]["text"]
        self.assertNotEqual(canned, question)
        argv = [
            sys.executable,
            str(ROOT / "tools/autocode.py"),
            "chat",
            "--workspace",
            str(workspace),
            "--run-dir",
            str(run.run_dir),
        ]
        with TerminalScreen(
            argv, cwd=workspace, env={**os.environ, **env}, cols=180, rows=100, tmux=require_tmux(self)
        ) as screen:
            shown = screen.wait_for(lambda shown: question in shown and "Type your reply" in shown)
            self.assertEqual(1, shown.count(question), shown)
            self.assertNotIn(canned, shown)
            screen.paste(BRIEF)
            screen.keys("Enter")
            shown = screen.wait_for(
                lambda shown: "Approve understanding and prepare plan" in shown and question in shown,
                90,
                what="the understanding gate with the answered question retained once",
            )
            self.assertEqual(1, shown.count(question), shown)
            screen.keys("C-d")
            self.assertEqual(2, screen.exit_status(15))
        before = run.status()
        self.assertIsNotNone(before["chat"]["review"])
        stages = before["cost"]["breakdown"]["by_stage"]
        self.assertEqual([("requirements_gather", 2)], [(row["stage"], row["stages"]) for row in stages])
        with TerminalScreen(
            argv, cwd=workspace, env={**os.environ, **env}, cols=180, rows=100, tmux=require_tmux(self)
        ) as screen:
            shown = screen.wait_for(
                lambda shown: "Approve understanding and prepare plan" in shown and question in shown
            )
            self.assertEqual(1, shown.count(question), shown)
            screen.keys("C-d")
            self.assertEqual(2, screen.exit_status(15))
        after = run.status()
        self.assertEqual(before["chat"], after["chat"])
        self.assertEqual(before["cost"], after["cost"])

    def test_typing_while_planner_runs_saves_one_message_and_shows_the_requirements_reply(self):
        workspace, env = fixture_workspace(self)
        provider = Path(env["PATH"].split(os.pathsep)[0]) / "codex"
        gate, ready, calls = (provider.parent / name for name in ("gate", "ready", "calls"))
        os.mkfifo(gate)
        marker = '    output = Path(sys.argv[sys.argv.index("-o") + 1])'
        amendment = textwrap.dedent(f"""
            with Path({str(calls)!r}).open('a') as log:
                log.write(stage + (' conversation' if data.get('message') else '') + '\\n')
            if stage == 'draft_plan' and not Path({str(ready)!r}).exists():
                Path({str(ready)!r}).write_text('ready')
                with Path({str(gate)!r}).open() as signal:
                    signal.read()
            if stage == 'requirements_gather' and data.get('message'):
                report = {{'message_id': data['message']['id'], 'contract_token': data['message']['contract_token'],
                           'intent': 'question', 'assistant_reply': {REPLY!r}}}
        """)
        self.assertIn(marker, provider.read_text())
        provider.write_text(provider.read_text().replace(marker, textwrap.indent(amendment, "    ") + "\n" + marker))
        run = taskrun.TaskRun.start(
            workspace, BRIEF, options=(*FIXTURE_OPTIONS, "--spec-first", "--no-adaptive-planning"), env=env, timeout=300
        )
        gathered = run.advance_until_input()
        question = gathered["needs"]["questions"][-1]
        run.answer(question["id"], "plan", resolver_token=gathered["needs"]["resolver_token"])
        argv = [
            sys.executable,
            str(ROOT / "tools/autocode.py"),
            "chat",
            "--workspace",
            str(workspace),
            "--run-dir",
            str(run.run_dir),
        ]
        with TerminalScreen(
            argv, cwd=workspace, env={**os.environ, **env}, cols=145, rows=100, tmux=require_tmux(self)
        ) as screen:
            screen.wait_for(
                lambda shown: ready.exists() and "Type a message" in shown,
                45,
                what="Planner is running with a live message composer",
            )
            screen.paste(QUESTION)
            screen.keys("Enter")
            screen.wait_for(lambda shown: "Message saved" in shown and "Requirements will reply" in shown, 15)
            with gate.open("w") as release:
                release.write("continue")
            screen.wait_for(
                lambda shown: "Approve this plan" in shown and "Reusing the conversion" in shown,
                90,
                what="Requirements reply before unchanged plan approval",
            )
            screen.keys("C-d")
            self.assertEqual(2, screen.exit_status(15))
        reviewed = run.status()
        self.assertEqual("approve_plan", reviewed["needs"]["kind"])
        text = [row["text"] for row in reviewed["chat"]["messages"]]
        self.assertEqual((1, 1), (text.count(QUESTION), text.count(REPLY)))
        stages = calls.read_text().splitlines()
        self.assertEqual(1, stages.count("draft_plan"), stages)
        self.assertEqual(1, stages.count("requirements_gather conversation"), stages)
        self.assertNotIn("builder", stages)
        reopened = taskrun.TaskRun(workspace, run.run_dir, env=env).status()
        self.assertEqual(reviewed["needs"]["token"], reopened["needs"]["token"])
        self.assertEqual(stages, calls.read_text().splitlines())
