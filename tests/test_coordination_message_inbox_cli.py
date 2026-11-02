"""Real task checkpoints stay unchanged while coordinator messages are queued."""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_coordination_message_inbox as inbox
import autocode_coordination_store as store
import autocode_taskrun as taskrun

from tests import ROOT
from tests.test_agent_step import fixture_workspace
from tests.test_taskrun import BRIEF, FIXTURE_OPTIONS


@unittest.skipUnless(sys.platform == "linux", "supervised provider requires supported Linux")
class MessageInboxCliTests(unittest.TestCase):
    def test_submissions_across_processes_do_not_advance_or_change_real_tasks(self):
        workspace, env = fixture_workspace(self)
        self.enterContext(patch.dict(os.environ, env))
        source = taskrun.TaskRun.start(
            workspace, BRIEF, options=(*FIXTURE_OPTIONS, "--spec-first", "--no-adaptive-planning"), env=env, timeout=240
        )
        original = source.advance_until_input()
        self.assertEqual("answer", original["needs"]["kind"])
        document = {
            "version": 1,
            "id": "inbox-fixture",
            "anchor": str(source.run_dir),
            "phase": "fixing",
            "members": [],
            "budget": {"cap_usd": 20},
        }
        store.save(document)
        saved = store.read(source.run_dir)
        code = (
            "import json,sys; import tests; import autocode_coordination_message_inbox as inbox; "
            "print(json.dumps(inbox.submit(sys.argv[1], request_id=sys.argv[2], text=sys.argv[3])))"
        )
        text = "Could the API reuse MarshalEPP and UnmarshalRegistry?"
        receipts = []
        with store.locked(source.run_dir):
            for identifier in ("first", "second", "first"):
                response = subprocess.run(
                    [sys.executable, "-c", code, str(source.run_dir), identifier, text],
                    cwd=ROOT,
                    env={**os.environ, **env},
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                self.assertEqual(0, response.returncode, response.stdout + response.stderr)
                receipts.append(json.loads(response.stdout))
        self.assertEqual(receipts[0]["receipt"], receipts[2]["receipt"])
        self.assertTrue(receipts[2]["idempotent"])
        self.assertEqual([row["receipt"] for row in receipts[:2]], inbox.read(source.run_dir))
        reopened = taskrun.TaskRun(source.workspace, source.run_dir, env=env).status()
        for key in ("status", "phase", "next_stage", "turn", "needs", "cost", "request_sources", "displayed_plan"):
            self.assertEqual(original.get(key), reopened.get(key), key)
        self.assertEqual(original["usage"]["accounting"], reopened["usage"]["accounting"])
        self.assertEqual(original["chat"]["messages"], reopened["chat"]["messages"])
        self.assertEqual(saved, store.read(source.run_dir))
        self.assertFalse((Path(workspace) / "greet.py").exists())
