"""Real CLI/TaskRun cost recovery with supervised offline providers (Linux)."""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_coordinate as coordinate
import autocode_coordination_store as store
import autocode_taskrun as taskrun

from .test_agent_step import fixture_workspace
from .test_taskrun import BRIEF, FIXTURE_OPTIONS


class SharedBudgetCliTests(unittest.TestCase):
    def test_real_member_cost_pause_resumes_with_retained_spend_and_no_plan_approval(self):
        first, env = fixture_workspace(self)
        second, _ = fixture_workspace(self)
        first, second = first.resolve(), second.resolve()
        probe = first.parent / "provider-launches.jsonl"
        provider = Path(env["PATH"].split(os.pathsep)[0]) / "codex"
        original = provider.with_name("fixture-provider.py")
        provider.rename(original)
        provider.write_text(
            f"#!{sys.executable}\nimport os, sys\nfrom pathlib import Path\n"
            f"with Path({str(probe)!r}).open('a') as output: output.write('launch\\n')\n"
            f"os.execv(sys.executable, [sys.executable, {str(original)!r}, *sys.argv[1:]])\n"
        )
        provider.chmod(0o755)
        self.enterContext(patch.dict(os.environ, env))
        stopped = taskrun.TaskRun.start(
            first,
            BRIEF,
            options=(*FIXTURE_OPTIONS, "--no-adaptive-planning", "--max-cost-usd", "0.000000001"),
            env=env,
            timeout=180,
        )
        before = stopped.advance_until_input()
        self.assertEqual("PAUSED_COST_LIMIT", (before.get("recovery") or {}).get("cause"), before["status"])
        self.assertGreater(before["cost"]["spent_usd"], 0)
        peer = taskrun.TaskRun.start(
            second,
            BRIEF,
            options=(*FIXTURE_OPTIONS, "--no-adaptive-planning"),
            env=env,
            timeout=180,
        )
        peer_before = peer.advance_until_input()
        self.assertEqual("approve_plan", peer_before["needs"]["kind"])
        members = [
            {
                "target": name,
                "workspace": str(run.workspace),
                "run_dir": str(run.run_dir),
                "base": "fixture-base",
                "started": True,
                "brief": BRIEF,
                "branch": "fixture-" + name,
                "repository": ["fixture", name],
            }
            for name, run in (("checkout", stopped), ("history", peer))
        ]
        document = {
            "version": 1,
            "id": "public-budget-recovery",
            "anchor": str(stopped.run_dir),
            "phase": "fixing",
            "task": BRIEF,
            "initial_scope": [],
            "choices": {},
            "members": members,
            "joint_attempts": [],
            "budget": {"cap_usd": 0.000000001, "initial_spent_usd": 0.0, "initial_complete": True},
        }
        # Only coordinator-owned fixture records are prepared here. Both task
        # states, their spending, tokens and gates came from the actual CLI.
        coordinate.sync_set(document)
        store.save(document)

        def cli(operation, *flags):
            reply = subprocess.run(
                [*taskrun.AUTOCODE, "coordinate", operation, "--run-dir", str(stopped.run_dir), *flags, "--json"],
                env={**os.environ, **env},
                text=True,
                capture_output=True,
                timeout=180,
            )
            return reply, json.loads(reply.stdout)

        reply, view = cli("status")
        self.assertEqual(0, reply.returncode, reply.stderr)
        offer = view["needs"]
        self.assertEqual("budget", offer["kind"])
        self.assertGreater(offer["known_spent_usd"], before["cost"]["spent_usd"])
        reply, revised = cli("budget", "--token", offer["token"], "--max-cost-usd", "2")
        self.assertEqual(0, reply.returncode, reply.stdout + reply.stderr)
        self.assertNotEqual("budget", revised["needs"]["kind"])
        after = stopped.status()
        self.assertEqual("approve_plan", after["needs"]["kind"], after.get("stop_reason"))
        self.assertEqual(before["routes"], after["routes"])
        self.assertGreaterEqual(after["cost"]["spent_usd"], before["cost"]["spent_usd"])
        self.assertEqual(peer_before["needs"]["token"], peer.status()["needs"]["token"])
        self.assertFalse(after["done"])
        self.assertFalse((first / "greet.py").exists(), "A budget decision cannot approve implementation")
        launches = probe.read_bytes()
        reply, _ = cli("budget", "--token", offer["token"], "--max-cost-usd", "3")
        self.assertEqual(2, reply.returncode)
        self.assertEqual(launches, probe.read_bytes(), "A stale budget command cannot launch another provider")


if __name__ == "__main__":
    unittest.main()
