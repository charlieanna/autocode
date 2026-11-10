"""The public resume action corrects a retained read-only task's write handoff."""

import json
import shlex
import unittest

import autocode_source_scope as source_scope
import autocode_support as support

from . import test_planning, test_subprocess
from .test_validation_scope_recovery import retain_misroute


class RetainedScopeCLI(unittest.TestCase):
    setUp = test_subprocess.SubprocessFlow.setUp
    launch = test_subprocess.SubprocessFlow.launch
    saved = test_subprocess.SubprocessFlow.saved
    prepare = test_planning.NativeJointFlow.prepare
    draft = test_planning.JointFlow.draft
    new_run_engine_args = test_planning.NativeJointFlow.new_run_engine_args

    def test_named_resume_queues_scope_review_without_a_provider_or_more_authority(self):
        run, state = self.draft()
        self.launch(["--run-dir", str(run), "--approve-goal", state["displayed_goal"]], 0)
        _, state = self.saved()
        revision = source_scope.snapshot(self.project, state, base_snapshot=support.snapshot)["revision"]
        retain_misroute(state, run, revision)
        # This is a synthetic legacy checkpoint, not a live run. Subsequent
        # observations and recovery use only the public CLI and its named action.
        (run / "state.json").write_text(json.dumps(state))
        before = json.loads(self.launch(["--run-dir", str(run), "--status"], 0).stdout)["view"]
        self.assertEqual("continue", before["needs"]["kind"])
        self.assertIsNone(before["chat"]["gate"])
        self.assertIsNone(before["chat"]["input"])
        self.assertIn("scope review", before["stop_reason"])
        result = self.launch(shlex.split(before["next_command"])[1:], 2)
        after = json.loads(self.launch(["--run-dir", str(run), "--status"], 0).stdout)["view"]
        self.assertEqual("review_completion", after["next_stage"])
        self.assertFalse(after["done"])
        self.assertIn("existing exact-plan review and approval", result.stdout)
        self.assertEqual(before["usage"]["accounting"]["attempts"], after["usage"]["accounting"]["attempts"])
        self.assertEqual(before["routes"], after["routes"])
        self.assertEqual(before["evidence"]["findings"], after["evidence"]["findings"])
        self.assertEqual(before["evidence"]["acceptance"], after["evidence"]["acceptance"])
        _, saved = self.saved()
        for key in (
            "settings",
            "goal_contract",
            "current_task",
            "no_progress_batches",
            "iteration",
            "validation",
            "regression_proof",
            "stages",
        ):
            self.assertEqual(state[key], saved[key], key)
        self.assertEqual(state["regression_proofs"], saved["regression_proofs"][:-1])
        self.assertEqual("reviewer", saved["regression_proofs"][-1]["action"])
