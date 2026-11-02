"""The public task conversation replies without rewriting the source run."""

import json
import os
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

import autocode_taskrun as taskrun
import autocode_util as util

from tests import ROOT
from tests.test_agent_step import fixture_workspace
from tests.test_message_context import context
from tests.test_taskrun import BRIEF, FIXTURE_OPTIONS


@unittest.skipUnless(sys.platform == "linux", "supervised provider requires supported Linux")
class TaskMessageContextCliTests(unittest.TestCase):
    def test_source_answers_questions_and_routes_changes_once_without_local_replanning(self):
        workspace, env = fixture_workspace(self)
        provider = Path(env["PATH"].split(os.pathsep)[0]) / "codex"
        calls = provider.parent / "context-calls.jsonl"
        marker = '    output = Path(sys.argv[sys.argv.index("-o") + 1])'
        amendment = textwrap.dedent(f"""
            with Path({str(calls)!r}).open('a') as log:
                log.write(json.dumps({{'stage': stage, 'message': data.get('message')}}) + '\\n')
            if stage == 'requirements_gather' and data.get('message'):
                message = data['message']
                assert message['context']['participants'][0]['target'] == 'gateway'
                changing = message['text'].startswith('Please change')
                report = {{'message_id': message['id'], 'contract_token': message['contract_token'],
                          'intent': 'change' if changing else 'question',
                          'assistant_reply': 'This needs gateway plan review.' if changing else 'Reuse needs assessment; it is not a requirement yet.',
                          'affected_targets': ['gateway'] if changing else []}}
        """)
        self.assertIn(marker, provider.read_text())
        provider.write_text(provider.read_text().replace(marker, textwrap.indent(amendment, "    ") + "\n" + marker))
        run = taskrun.TaskRun.start(
            workspace, BRIEF, options=(*FIXTURE_OPTIONS, "--spec-first", "--no-adaptive-planning"), env=env, timeout=240
        )
        original = run.advance_until_input()
        self.assertEqual("answer", original["needs"]["kind"], original["needs"])
        packet = workspace / ".autocode/public-participants.json"
        packet.write_text(json.dumps(context()))
        before_calls = [json.loads(line) for line in calls.read_text().splitlines()]
        for index, text in enumerate(
            ("Could we reuse the conversion?", "Please change the gateway to preserve the result.")
        ):
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
                f"task-message-{index}",
                "--kind",
                "message",
                "--text",
                text,
                "--context-file",
                str(packet),
                "--json",
            ]
            submitted = subprocess.run(command, env={**os.environ, **env}, capture_output=True, text=True, timeout=30)
            self.assertEqual(0, submitted.returncode, submitted.stdout + submitted.stderr)
            message_id = json.loads(submitted.stdout)["receipt"]["message_id"]
            for _ in range(4):
                view = run.advance()
                if any(row["id"] == message_id + "-reply-after" for row in view["chat"]["messages"]):
                    break
            else:
                self.fail(str(view["needs"]))
            response = next(row for row in view["chat"]["messages"] if row["id"] == message_id + "-reply-after")
            self.assertEqual(
                {
                    "request_id": f"task-message-{index}",
                    "intent": "question" if not index else "change",
                    "affected_targets": [] if not index else ["gateway"],
                    "context_sha256": util.digest(context()),
                },
                response["conversation"],
            )
            for key in ("status", "phase", "next_stage", "request_sources", "approved_contract", "turn", "scope"):
                self.assertEqual(original.get(key), view.get(key), key)
            self.assertEqual(original["needs"]["questions"], view["needs"]["questions"])
            retry = subprocess.run(command, env={**os.environ, **env}, capture_output=True, text=True, timeout=30)
            self.assertEqual(0, retry.returncode, retry.stdout + retry.stderr)
            self.assertTrue(json.loads(retry.stdout)["idempotent"])
            reopened = taskrun.TaskRun(run.workspace, run.run_dir, env=env).status()
            self.assertEqual(view["chat"]["messages"], reopened["chat"]["messages"])
        records = [json.loads(line) for line in calls.read_text().splitlines()]
        self.assertEqual(before_calls, [row for row in records if not row.get("message")])
        self.assertEqual(2, len([row for row in records if row.get("message")]))
        self.assertFalse((workspace / "greet.py").exists(), "An assessment must not start a Builder")
