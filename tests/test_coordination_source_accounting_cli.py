"""Real source replies remain charged to the same coordinated task cap."""

import json
import os
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_coordination_budget as budget
import autocode_coordination_store as store
import autocode_taskrun as taskrun

from tests.test_agent_step import fixture_workspace
from tests.test_message_context import context
from tests.test_taskrun import BRIEF, FIXTURE_OPTIONS


@unittest.skipUnless(sys.platform == "linux", "supervised provider requires supported Linux")
class SourceAccountingCliTests(unittest.TestCase):
    def test_source_reply_spend_stops_further_coordination_without_replaying_work(self):
        workspace, env = fixture_workspace(self)
        peer_workspace, _ = fixture_workspace(self)
        self.enterContext(patch.dict(os.environ, env))
        provider = Path(env["PATH"].split(os.pathsep)[0]) / "codex"
        calls = provider.parent / "source-cost-calls"
        marker = '    output = Path(sys.argv[sys.argv.index("-o") + 1])'
        addition = textwrap.dedent(f"""
            with Path({str(calls)!r}).open('a') as output:
                output.write(stage + '\\n')
            if stage == 'requirements_gather' and data.get('message'):
                report = {{'message_id': data['message']['id'], 'contract_token': data['message']['contract_token'],
                    'intent': 'question', 'assistant_reply': 'This is an assessment, not a change.', 'affected_targets': []}}
        """)
        self.assertIn(marker, provider.read_text())
        provider.write_text(provider.read_text().replace(marker, textwrap.indent(addition, "    ") + "\n" + marker))
        source = taskrun.TaskRun.start(
            workspace, BRIEF, options=(*FIXTURE_OPTIONS, "--spec-first", "--no-adaptive-planning"), env=env, timeout=240
        )
        before = source.advance_until_input()
        self.assertEqual("answer", before["needs"]["kind"], before["needs"])
        peer = taskrun.TaskRun.start(
            peer_workspace, BRIEF, options=(*FIXTURE_OPTIONS, "--no-adaptive-planning"), env=env, timeout=240
        )
        planned = peer.advance_until_input()
        self.assertEqual("approve_plan", planned["needs"]["kind"])
        initial_total = before["cost"]["spent_usd"] + planned["cost"]["spent_usd"]
        saved_budget = budget.initial(before)
        saved_budget.update(
            source_run_dir=str(source.run_dir),
            cap_usd=initial_total + 0.000000001,
            planning_allowance={"cap_usd": initial_total + 0.000000001, "initial": budget.planning_spend(before)},
        )
        document = {
            "version": 1,
            "id": "source-accounting-fixture",
            "anchor": str(source.run_dir),
            "phase": "fixing",
            "task": BRIEF,
            "initial_scope": [],
            "choices": {},
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
            "budget": saved_budget,
        }
        store.save(document)
        packet = workspace / ".autocode/public-context.json"
        packet.write_text(json.dumps(context()))
        command = [
            *taskrun.AUTOCODE,
            "intervention",
            "submit",
            "--workspace",
            str(source.workspace),
            "--run-dir",
            str(source.run_dir),
            "--request-id",
            "source-cost-question",
            "--kind",
            "message",
            "--text",
            "What do you think of reusing the converter?",
            "--context-file",
            str(packet),
            "--json",
        ]
        submitted = subprocess.run(command, env={**os.environ, **env}, capture_output=True, text=True, timeout=30)
        self.assertEqual(0, submitted.returncode, submitted.stdout + submitted.stderr)
        message_id = json.loads(submitted.stdout)["receipt"]["message_id"]
        for _ in range(4):
            after = source.advance()
            if any(row["id"] == message_id + "-reply-after" for row in after["chat"]["messages"]):
                break
        else:
            self.fail(str(after["needs"]))
        self.assertGreater(after["cost"]["spent_usd"], before["cost"]["spent_usd"])
        launches = calls.read_bytes()
        for operation in ("status", "advance", "status"):
            reply = subprocess.run(
                [*taskrun.AUTOCODE, "coordinate", operation, "--run-dir", str(source.run_dir), "--json"],
                env={**os.environ, **env},
                capture_output=True,
                text=True,
                timeout=120,
            )
            self.assertEqual(0, reply.returncode, reply.stdout + reply.stderr)
            public = json.loads(reply.stdout)
            self.assertEqual("blocked", public["needs"]["kind"], public["needs"])
            self.assertIn("cost cap is exhausted", public["needs"]["reason"])
            self.assertIsNone(public["next_command"])
            ledgers = public["accounting"]["runs"]
            self.assertEqual(2, len(ledgers))
            self.assertAlmostEqual(
                after["cost"]["spent_usd"] + planned["cost"]["spent_usd"],
                sum(row["cost"]["spent_usd"] for row in ledgers),
            )
        self.assertEqual(launches, calls.read_bytes())
        self.assertEqual(planned["needs"]["token"], peer.status()["needs"]["token"])
        self.assertEqual(before["request_sources"], source.status()["request_sources"])
        self.assertEqual(saved_budget, store.read(source.run_dir)["budget"])
        self.assertFalse((workspace / "greet.py").exists())
