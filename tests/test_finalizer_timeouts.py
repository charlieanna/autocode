"""Final plan review idle timeouts through the real CLI, admission and recovery (#453).

The runner, its planning allowance, refunds, archives and AutoResolver boundaries are
real. Planning stages run the offline fake provider; a silent final review is a
provider that never answers, and its idle deadline is reported by a simulated waiter
instead of waiting in real time. No model is contacted.
"""

import contextlib
import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode as runner
import autocode_goals as goals
import autocode_support as support
from goal_fixtures import assert_operational_wait

from . import test_subprocess
from .supervision_fixture import launcher


class Silent:
    """A provider that started a session and then produced nothing more."""

    pid = 987654321  # Above any real pid_max: liveness checks see no such process.

    def __init__(self, command, **kwargs):
        kwargs["stdout"].write(json.dumps({"type": "thread.started", "thread_id": "silent"}) + "\n")
        kwargs["stdout"].flush()


class FinalizerTimeoutTests(unittest.TestCase):
    setUp = test_subprocess.SubprocessFlow.setUp
    saved = test_subprocess.SubprocessFlow.saved

    def autocode(self, *args):
        argv = ["autocode", "--workspace", str(self.project), *test_subprocess.with_resolver_token(args)]
        output = io.StringIO()
        with (
            patch.object(sys, "argv", argv),
            patch.dict(os.environ, self.env, clear=True),
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(output),
        ):
            code = runner.main()
        self.output = output.getvalue()
        return code

    def plan(self, silent_finals):
        """Plan with the fixed pipeline; the first ``silent_finals`` final reviews never answer."""
        self.finals = []
        real_launch, real_wait = runner.supervision.launch, runner.processes.wait_for_stage

        @contextlib.contextmanager
        def launch(command, **options):
            prompt = Path(getattr(options.get("stdin"), "name", "") or "").name
            if prompt.startswith("plan-finalize-"):
                self.finals.append(prompt)
                if len(self.finals) <= silent_finals:
                    with launcher(Silent)(command, **options) as child:
                        yield child
                    return
            with real_launch(command, **options) as child:
                yield child

        def wait(child, hard_limit, checkpoint, *, activity, activity_checkpoint, **options):
            if not isinstance(child, Silent):
                return real_wait(
                    child, hard_limit, checkpoint, activity=activity, activity_checkpoint=activity_checkpoint, **options
                )
            # Shaped like autocode_process.stop_at_deadline when the idle limit fires.
            checkpoint([])
            activity.timeout = {"kind": "idle", "reason": activity.idle_reason()}
            activity_checkpoint(
                {
                    **activity.poll(),
                    "activity": "stalled",
                    "timeout_kind": "idle",
                    "timeout_reason": activity.timeout["reason"],
                }
            )
            return -15, True

        self.env["AUTOCODE_FIXTURE_MODE"] = "no-human"
        self.env["AUTOCODE_REGISTRY_LAUNCH_PROBE"] = str(self.root / "launches.jsonl")
        with patch.dict(os.environ, self.env, clear=True):
            transport = support.local_settings()  # The offline provider's login, read once.
        self.patches = contextlib.ExitStack()
        self.addCleanup(self.patches.close)
        self.patches.enter_context(patch.object(support, "local_settings", return_value=transport))
        self.patches.enter_context(patch.object(runner.supervision, "launch", launch))
        self.patches.enter_context(patch.object(runner.processes, "wait_for_stage", wait))
        # Adaptive planning saves its sized allowance unmarked, which AutoResolver treats as
        # protected. The fixed pipeline keeps the runner default, so planning recovery is live.
        self.assertEqual(
            2,
            self.autocode(
                "--engine",
                "codex",
                "--joint-planning",
                "--no-adaptive-planning",
                "--plan-reviewer-model",
                "gpt-6-astra",
                "Build a greeting tool",
                "--no-chat",
                "--in-place",
            ),
            self.output,
        )
        run, _ = self.saved()
        self.assertEqual(0, self.autocode("--run-dir", str(run), "--answer", "Q1=CLI"), self.output)
        code = self.autocode("--run-dir", str(run), "--no-chat")
        return run, code

    def launched(self):
        """Stages the offline provider actually answered, in order."""
        path = self.root / "launches.jsonl"
        return [json.loads(line)["stage"] for line in path.read_text().splitlines()] if path.exists() else []

    def finalizer_attempts(self, state):
        return [row for row in state["stages"] if row.get("stage") == "astra_finalize"]

    def assert_no_planning_recovery_grant(self, state):
        self.assertFalse(state["planning"].get("recovery_review_grants"))
        self.assertFalse(state["planning"].get("recovery_review_calls_used"))
        self.assertFalse(
            [
                row
                for row in state["stages"]
                if (row.get("receipt") or {}).get("scope") == "planning_operational_recovery"
            ]
        )

    def test_two_finalizer_idle_timeouts_recover_to_explicit_plan_approval(self):
        run, code = self.plan(silent_finals=2)
        self.assertEqual(2, code, self.output)
        _, state = self.saved()
        self.assertEqual("AWAITING_GOAL_APPROVAL", state["status"], state.get("stop_reason"))
        self.assertEqual(
            ["plan-finalize-01.prompt.md", "plan-finalize-02.prompt.md", "plan-finalize-03.prompt.md"], self.finals
        )
        attempts = self.finalizer_attempts(state)
        self.assertEqual([True, True, False], [bool(row.get("timed_out")) for row in attempts])
        for failed in attempts[:2]:
            self.assertEqual("idle", failed["timeout_kind"])
            self.assertTrue(failed["abandoned"] and failed["automatic_recovery"])
            # Neither silent review returned a report, so each gave its call back.
            self.assertTrue(failed["planning_review_refunded"])
        # The allowance holds exactly the two reviews that reported: the challenge and the final.
        self.assertEqual(2, state["planning"]["astra_calls"])
        self.assertEqual(2, runner.planning.review_call_limit(state))
        self.assert_no_planning_recovery_grant(state)
        # No approval is implied: the final plan waits for the person, and nothing was built.
        self.assertFalse(goals.approved(state))
        self.assertEqual(state["displayed_goal"], state["planning"]["final_token"])
        self.assertEqual(["astra_challenge", "glm_revise", "astra_finalize"], self.launched()[-3:])
        self.assertFalse((self.project / "greet.py").exists())
        reviewed = goals.token({"revision": attempts[0]["contract_revision"], "hash": attempts[0]["contract_hash"]})
        self.assertNotEqual(reviewed, state["displayed_goal"])
        self.assertEqual(2, self.autocode("--run-dir", str(run), "--approve-goal", reviewed), self.output)
        self.assertFalse(goals.approved(self.saved()[1]))
        self.assertEqual(
            0, self.autocode("--run-dir", str(run), "--approve-goal", state["displayed_goal"]), self.output
        )
        approved = self.saved()[1]
        self.assertTrue(goals.approved(approved))
        self.assertEqual(3, len(self.finals))
        self.assertNotIn("terra", self.launched())
        self.assertNotIn("terra", [row["stage"] for row in approved["stages"]])

    def test_repeated_finalizer_idle_timeouts_remain_bounded(self):
        run, code = self.plan(silent_finals=99)
        self.assertEqual(2, code, self.output)
        _, state = self.saved()
        self.assertEqual(3, len(self.finals))
        self.assertEqual(3, sum(bool(row.get("timed_out")) for row in self.finalizer_attempts(state)))
        public = assert_operational_wait(self, state, "PAUSED_TIMEOUT_RECOVERY")
        self.assertIn("--grant-recovery", public["request"]["decision_needed"])
        self.assertLessEqual(state["planning"]["astra_calls"], runner.planning.review_call_limit(state))
        self.assert_no_planning_recovery_grant(state)
        self.assertFalse(goals.approved(state))
        self.assertIsNone(state["planning"]["final_token"])
        # Neither another invocation nor a plain resume launches a fourth final review.
        self.assertEqual(2, self.autocode("--run-dir", str(run), "--no-chat"), self.output)
        self.assertEqual(2, self.autocode("--run-dir", str(run), "--resume-paused", "--no-chat"), self.output)
        self.assertEqual(3, len(self.finals))
        stopped = self.saved()[1]
        self.assertFalse(goals.approved(stopped))
        self.assert_no_planning_recovery_grant(stopped)
        self.assertEqual(["astra_challenge", "glm_revise"], self.launched()[-2:])


if __name__ == "__main__":
    unittest.main()
