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
    def test_cancelled_real_members_can_resume_uncapped_without_repeating_saved_planning(self):
        self.exercise_recovery(planning=False, cancelled=True)

    def test_real_member_cost_pause_resumes_with_retained_spend_and_no_plan_approval(self):
        self.exercise_recovery(planning=False)

    def test_real_planning_reallocation_retains_work_and_requires_separate_pause_acknowledgement(self):
        self.exercise_recovery(planning=True)

    def test_real_reallocated_planning_stops_again_without_consuming_the_whole_set_cap(self):
        self.exercise_recovery(planning=True, exhaust=True)

    def exercise_recovery(self, *, planning, exhaust=False, cancelled=False):
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
        if cancelled:
            reply, _ = cli("cancel")
            self.assertEqual(0, reply.returncode)
            reply, held = cli("status")
            self.assertEqual("coordination_resume", held["needs"]["kind"], held)
            launches = probe.read_bytes()
            token = held["needs"]["token"]
            reply, resumed = cli("resume", "--token", token, "--max-cost-usd", "0")
            self.assertEqual(0, reply.returncode, resumed)
            self.assertEqual(launches, probe.read_bytes())
            self.assertEqual(before["cost"], stopped.status()["cost"])
            self.assertEqual(peer_before["needs"]["token"], peer.status()["needs"]["token"])
            _, reopened = cli("status")
            self.assertEqual(resumed["needs"]["kind"], reopened["needs"]["kind"])
            self.assertEqual(resumed["needs"]["target"], reopened["needs"]["target"])
            self.assertEqual(resumed["needs"]["view"]["needs"], reopened["needs"]["view"]["needs"])
            self.assertEqual(resumed["needs"]["view"]["cost"], reopened["needs"]["view"]["cost"])
            reply, _ = cli("resume", "--token", token, "--max-cost-usd", "0")
            self.assertEqual(2, reply.returncode)
            self.assertEqual(launches, probe.read_bytes())
            # Public child recovery applies the explicitly selected no-cap policy.
            # The coordinator recovery itself neither starts nor approves this work.
            stopped.options = ("--max-cost-usd", "0")
            after = stopped.resume_paused()
            self.assertEqual("approve_plan", after["needs"]["kind"], after["stop_reason"])
            self.assertEqual(before["routes"], after["routes"])
            prior = before["usage"]["accounting"]["attempts"]
            current = after["usage"]["accounting"]["attempts"]
            self.assertEqual([x["identity"] for x in prior], [x["identity"] for x in current[: len(prior)]])
            self.assertFalse(after.get("approved_contract"))
            self.assertTrue(all(row["kind"] == "resume" for row in after["approvals"]))
            self.assertFalse((first / "greet.py").exists())
            self.assertFalse(store.cancelled(stopped.run_dir))
            return
        if planning:
            # The same real child stop under the new policy must retain the remaining
            # task money rather than offering a loop of same-cap acknowledgements.
            legacy_budget = dict(document["budget"])
            whole_cap = 10 if exhaust else 2
            document["budget"] = {
                **legacy_budget,
                "cap_usd": whole_cap,
                "planning_allowance": {"cap_usd": 0.000000001, "initial": {"spent_usd": 0, "complete": True}},
            }
            store.save(document)
            launches = probe.read_bytes()
            for operation in ("status", "advance"):
                reply, held = cli(operation)
                self.assertEqual(0, reply.returncode, reply.stdout + reply.stderr)
                self.assertEqual("planning_budget", held["needs"]["kind"])
                self.assertIsNone(held["next_command"])
                self.assertEqual(launches, probe.read_bytes())
            allocation = held["needs"]
            reply, refused = cli(
                "planning-budget", "--token", allocation["token"], "--max-planning-cost-usd", str(whole_cap + 1)
            )
            self.assertEqual(2, reply.returncode, refused)
            self.assertEqual(launches, probe.read_bytes())
            reply, reallocated = cli("planning-budget", "--token", allocation["token"], "--max-planning-cost-usd", "2")
            self.assertEqual(0, reply.returncode, reallocated)
            self.assertEqual(launches, probe.read_bytes(), "Reallocation itself launches and approves nothing")
            retained = store.read(stopped.run_dir)
            self.assertEqual(whole_cap, retained["budget"]["cap_usd"])
            self.assertEqual(2, retained["budget"]["planning_allowance"]["cap_usd"])
            self.assertEqual(before["cost"], stopped.status()["cost"])
            self.assertEqual(peer_before["needs"]["token"], peer.status()["needs"]["token"])
            reply, _ = cli("planning-budget", "--token", allocation["token"], "--max-planning-cost-usd", "2")
            self.assertEqual(2, reply.returncode)
            self.assertEqual(retained, store.read(stopped.run_dir), "A duplicate cannot replenish the allocation")
            _, reopened = cli("status")
            self.assertEqual(reallocated["needs"]["token"], reopened["needs"]["token"])
            if exhaust:
                # Offline billing fault injection: the next actual provider call
                # is larger than its planning allocation but below the task cap.
                source = original.read_text()
                usage = '"input_tokens": 10, "output_tokens": 10'
                self.assertIn(usage, source)
                original.write_text(source.replace(usage, '"input_tokens": 1000000, "output_tokens": 100000'))
            # The existing separate cost-pause acknowledgement resumes this same
            # child; reallocation does not consume or manufacture its recovery token.
            reply, resumed = cli("budget", "--token", reopened["needs"]["token"], "--max-cost-usd", str(whole_cap))
            self.assertEqual(0, reply.returncode, resumed)
            if exhaust:
                self.assertEqual("planning_budget", resumed["needs"]["kind"], resumed)
                self.assertGreater(resumed["needs"]["planning_spent_usd"], 2)
                self.assertLess(resumed["needs"]["known_spent_usd"], whole_cap)
                launches = probe.read_bytes()
                reply, held = cli("advance")
                self.assertEqual(0, reply.returncode, held)
                self.assertEqual("planning_budget", held["needs"]["kind"])
                self.assertEqual(launches, probe.read_bytes())
                self.assertEqual(2, store.read(stopped.run_dir)["budget"]["planning_allowance"]["cap_usd"])
                self.assertEqual(peer_before["needs"]["token"], peer.status()["needs"]["token"])
                return
            self.assertEqual("approve_plan", stopped.status()["needs"]["kind"])
            self.assertGreaterEqual(stopped.status()["cost"]["spent_usd"], before["cost"]["spent_usd"])
            self.assertFalse((first / "greet.py").exists())
            return
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
