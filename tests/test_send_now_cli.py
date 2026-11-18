"""Send now: a message that ends the running stage and is answered at once (#3259).

A message typed while the Builder worked waited for the Builder to finish, and Stop now plus a queued
message left the run paused for a Resume that said it "runs it again". A message submitted through the
view's ``chat.input.now_command`` ends the stage as Stop now does and goes straight to Requirements:
no pause, and a change goes back through planning, so the Builder never acts on the message's text."""

import json
import os
import shlex
import subprocess
import sys
import textwrap
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import autocode_stop as stop
import autocode_taskrun as taskrun

from tests import ROOT
from tests.test_agent_step import fixture_workspace
from tests.test_taskrun import BRIEF, FIXTURE_OPTIONS

MESSAGE = "Also add a --loud flag"


@unittest.skipUnless(sys.platform == "linux", "supervised provider requires supported Linux")
class SendNowCliTests(unittest.TestCase):
    def run_until_builder_and_send_now(self, intent, reply):
        workspace, env = fixture_workspace(self)
        provider = Path(env["PATH"].split(os.pathsep)[0]) / "codex"
        root = provider.parent
        entered, calls = root / "builder-entered", root / "calls"
        marker = '    output = Path(sys.argv[sys.argv.index("-o") + 1])'
        # The first Builder never finishes on its own: only the person's Send now ends it.
        amendment = textwrap.dedent(f"""
            with Path({str(calls)!r}).open('a') as log:
                log.write(stage + (' conversation' if data.get('message') else '') + '\\n')
            if stage == 'builder' and not Path({str(entered)!r}).exists():
                Path({str(entered)!r}).write_text('entered')
                import time as _time
                _time.sleep(600)
            if stage == 'requirements_gather' and data.get('message'):
                report = {{'message_id': data['message']['id'], 'contract_token': data['message']['contract_token'],
                           'intent': {intent!r}, 'assistant_reply': {reply!r}}}
        """)
        self.assertIn(marker, provider.read_text())
        provider.write_text(provider.read_text().replace(marker, textwrap.indent(amendment, "    ") + "\n" + marker))
        run = taskrun.TaskRun.start(
            workspace, BRIEF, options=(*FIXTURE_OPTIONS, "--spec-first", "--no-adaptive-planning"), env=env, timeout=300
        )
        gathered = run.advance_until_input()
        question = gathered["needs"]["questions"][-1]
        run.answer(question["id"], "plan", resolver_token=gathered["needs"]["resolver_token"])
        planned = run.advance_until_input()
        self.assertEqual("approve_plan", planned["needs"]["kind"], planned["needs"])
        run.approve_plan(planned["needs"]["token"])
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(run.advance_until_input)
            deadline = time.monotonic() + 120
            while not entered.exists() and not future.done() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(entered.exists(), future.result() if future.done() else "the Builder never started")
            offer = run.status()["chat"]["input"]
            self.assertEqual("message", offer["kind"])
            words = [
                word.replace("REQUEST_ID", "send-now").replace("MESSAGE", MESSAGE)
                for word in shlex.split(offer["now_command"])
            ]
            self.assertEqual("autocode", words[0])
            submitted = subprocess.run(
                [sys.executable, str(ROOT / "tools/autocode.py"), *words[1:]],
                env={**os.environ, **env},
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(0, submitted.returncode, submitted.stdout + submitted.stderr)
            self.assertTrue(json.loads(submitted.stdout)["receipt"]["now"])
            # Without Send now the sleeping Builder holds the run far past this bound.
            reached = future.result(timeout=180)
        return run, reached, calls.read_text().splitlines()

    def test_send_now_ends_the_builder_and_a_change_goes_back_through_the_plan_without_a_pause(self):
        run, reached, stages = self.run_until_builder_and_send_now("change", "I will add a --loud flag.")
        self.assertEqual(("AWAITING_GOAL_APPROVAL", "approve_plan"), (reached["status"], reached["needs"]["kind"]))
        texts = [row["text"] for row in reached["chat"]["messages"]]
        self.assertEqual(1, texts.count(MESSAGE))
        self.assertIn("I will add a --loud flag.", texts)
        builder = stages.index("builder")
        self.assertEqual("requirements_gather conversation", stages[builder + 1], stages)
        self.assertEqual(1, stages.count("builder"), "the Builder never acts on the message before a new plan")
        state = json.loads((run.run_dir / "state.json").read_text())
        ended = [row for row in state["stages"] if row["stage"] == "builder"]
        self.assertEqual(("person", True), (ended[0]["timeout_kind"], ended[0].get("abandoned")))
        self.assertNotEqual(stop.STOPPED_NOW_REASON, state.get("stop_reason"), "no pause for the person to resume")

    def test_send_now_question_is_answered_and_the_ended_builder_runs_again(self):
        _, reached, stages = self.run_until_builder_and_send_now("question", "It prints one line.")
        self.assertEqual("TASK_COMPLETE", reached["status"], reached["needs"])
        builder = stages.index("builder")
        self.assertEqual(["requirements_gather conversation", "builder"], stages[builder + 1 : builder + 3], stages)
        texts = [row["text"] for row in reached["chat"]["messages"]]
        self.assertIn("It prints one line.", texts)


if __name__ == "__main__":
    unittest.main()
