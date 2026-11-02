"""Public cancellation recovery with a read-only participant and a dispatch sentinel.

The fake participant exposes an archived attempt through TaskRun's public status
contract. Every non-status invocation fails: recovery may not retry that attempt.
This verifies coordinator recovery, not provider interruption or cleanup itself.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_coordination_store as store

from tests import ROOT
from tests.test_agent_step import fixture_workspace


class CancelledRetryCliTests(unittest.TestCase):
    def test_uncapped_recovery_keeps_unpriced_attempt_and_never_retries_the_child(self):
        workspace, env = fixture_workspace(self)
        peer, _ = fixture_workspace(self)
        workspace, peer = workspace.resolve(), peer.resolve()
        self.enterContext(patch.dict(os.environ, env))
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        anchor, child = workspace / ".autocode/runs/source", peer / ".autocode/runs/child"
        source = {
            "workspace": str(workspace),
            "run_dir": str(anchor),
            "status": "AWAITING_GOAL_APPROVAL",
            "needs": {"kind": "approve_plan"},
            "cost": {"spent_usd": 1, "complete": True},
        }
        held = {
            "workspace": str(peer),
            "run_dir": str(child),
            "status": "PAUSED_STAGE_ABANDONED",
            "needs": {"kind": "retry_job", "attempt_id": "001/tester-01", "job_retry_token": "exact-retry"},
            "recovery": {"cause": "PAUSED_STAGE_ABANDONED"},
            "liveness": {role: {"alive": False} for role in ("owner", "keeper", "provider")},
            "cost": {"spent_usd": 4, "complete": False, "unpriced_stages": 1},
            "usage": {"accounting": {"attempts": [{"identity": "archived", "active": False, "interrupted": True}]}},
        }
        observations = root / "observations.json"
        observations.write_text(json.dumps({str(anchor): source, str(child): held}))
        calls, participant = root / "calls", root / "participant.py"
        participant.write_text(
            "import json,sys\nfrom pathlib import Path\n"
            f"with Path({str(calls)!r}).open('a') as log:log.write(json.dumps(sys.argv[1:])+'\\n')\n"
            "assert '--status' in sys.argv, 'A child action was dispatched'\n"
            f"views=json.loads(Path({str(observations)!r}).read_text())\n"
            "print(json.dumps({'view':views[sys.argv[sys.argv.index('--run-dir')+1]]}))\n"
        )
        entry = root / "coordinate.py"
        entry.write_text(
            "import sys\n"
            f"sys.path.insert(0,{str(ROOT / 'tools')!r})\n"
            "import autocode_taskrun\n"
            f"autocode_taskrun.AUTOCODE=(sys.executable,{str(participant)!r})\n"
            "import autocode_coordinate\n"
            "raise SystemExit(autocode_coordinate.cli(sys.argv[1:]))\n"
        )
        store.save(
            {
                "version": 1,
                "id": "cancelled-retry-fixture",
                "anchor": str(anchor),
                "phase": "fixing",
                "task": "Fix history",
                "request_sources": [],
                "choices": {},
                "initial_scope": [],
                "joint_attempts": [],
                "members": [
                    {
                        "target": "peer",
                        "workspace": str(peer),
                        "run_dir": str(child),
                        "base": "fixture",
                        "started": True,
                    }
                ],
                "budget": {"initial_spent_usd": 1, "initial_complete": True, "cap_usd": 0},
            }
        )

        def invoke(operation, *args, expected=0):
            result = subprocess.run(
                [sys.executable, str(entry), operation, "--run-dir", str(anchor), *args, "--json"],
                env={**os.environ, **env},
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(expected, result.returncode, result.stdout + result.stderr)
            return json.loads(result.stdout)

        invoke("cancel")
        offer = invoke("status")["needs"]
        self.assertEqual("coordination_resume", offer["kind"])
        self.assertFalse(offer["complete"])
        before = observations.read_bytes()
        invoke("resume", "--token", offer["token"], "--max-cost-usd", "20", expected=2)
        self.assertEqual(offer["token"], invoke("status")["needs"]["token"])
        invoke("resume", "--token", offer["token"], "--max-cost-usd", "0")
        reopened = invoke("status")
        self.assertEqual("input", reopened["needs"]["kind"])
        self.assertEqual(held, reopened["needs"]["view"])
        self.assertEqual(before, observations.read_bytes())
        self.assertTrue(all("--status" in json.loads(line) for line in calls.read_text().splitlines()))
        self.assertFalse((peer / "greet.py").exists())
        repeated = invoke("resume", "--token", offer["token"], "--max-cost-usd", "0", expected=2)
        self.assertIn("changed", repeated["error"])
