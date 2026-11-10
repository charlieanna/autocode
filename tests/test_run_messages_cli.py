"""Messages arriving during Planner are answered once through the public runner."""

import json
import os
import subprocess
import sys
import textwrap
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import autocode_taskrun as taskrun

from tests import ROOT
from tests.test_agent_step import fixture_workspace
from tests.test_taskrun import BRIEF, FIXTURE_OPTIONS

QUESTION = "Could the existing conversion be reused? Explain the tradeoffs before changing the plan."
REPLY = "Reusing the conversion is an option to assess; this question does not change the reviewed requirements."


@unittest.skipUnless(sys.platform == "linux", "supervised provider requires supported Linux")
class LiveMessageCliTests(unittest.TestCase):
    def test_message_during_planner_answers_once_retains_work_and_reopens_without_replanning(self):
        workspace, env = fixture_workspace(self)
        provider = Path(env["PATH"].split(os.pathsep)[0]) / "codex"
        root = provider.parent
        gate, ready, calls = root / "message-gate", root / "message-ready", root / "message-calls"
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
        self.assertEqual("answer", gathered["needs"]["kind"], gathered["needs"])
        question = gathered["needs"]["questions"][-1]
        run.answer(question["id"], "plan", resolver_token=gathered["needs"]["resolver_token"])
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(run.advance_until_input)
            deadline = time.monotonic() + 60
            while not ready.exists() and not future.done() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(ready.exists(), future.result() if future.done() else "Planner did not reach its gate")
            command = [
                sys.executable,
                str(ROOT / "tools/autocode.py"),
                "intervention",
                "submit",
                "--workspace",
                str(run.workspace),
                "--run-dir",
                str(run.run_dir),
                "--request-id",
                "live-message",
                "--kind",
                "message",
                "--text",
                QUESTION,
                "--json",
            ]
            try:
                submitted = subprocess.run(
                    command, env={**os.environ, **env}, capture_output=True, text=True, timeout=30
                )
                self.assertEqual(0, submitted.returncode, submitted.stdout + submitted.stderr)
                self.assertTrue(json.loads(submitted.stdout)["accepted"])
            finally:
                with gate.open("w") as release:
                    release.write("continue")
            reviewed = future.result(timeout=240)
        self.assertEqual("approve_plan", reviewed["needs"]["kind"], (reviewed["status"], reviewed["needs"]))
        texts = [row["text"] for row in reviewed["chat"]["messages"]]
        self.assertEqual(1, texts.count(QUESTION))
        self.assertEqual(1, texts.count(REPLY))
        stages = calls.read_text().splitlines()
        self.assertEqual(1, stages.count("draft_plan"), stages)
        self.assertEqual(1, stages.count("requirements_gather conversation"), stages)
        self.assertFalse((workspace / "greet.py").exists(), "No question authorizes Builder")
        self.assertNotIn(QUESTION, "\n".join(row["text"] for row in reviewed["request_sources"]))
        retry = subprocess.run(command, env={**os.environ, **env}, capture_output=True, text=True, timeout=30)
        self.assertEqual(0, retry.returncode, retry.stderr)
        self.assertTrue(json.loads(retry.stdout)["idempotent"])
        reopened = taskrun.TaskRun(run.workspace, run.run_dir, env=env).status()
        self.assertEqual(reviewed["needs"]["token"], reopened["needs"]["token"])
        self.assertEqual(texts, [row["text"] for row in reopened["chat"]["messages"]])
        self.assertEqual(stages, calls.read_text().splitlines())
