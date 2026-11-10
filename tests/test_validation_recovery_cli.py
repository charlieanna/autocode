"""An upgraded saved validation limit settles through the named CLI action."""

import json
import shlex
import unittest
from copy import deepcopy
from unittest.mock import patch

import autocode as runner
import autocode_resolver_human as human
import autocode_source_scope as source_scope
import autocode_support as support

from . import test_planning, test_subprocess


class RetainedValidationCLI(unittest.TestCase):
    setUp = test_subprocess.SubprocessFlow.setUp
    launch = test_subprocess.SubprocessFlow.launch
    saved = test_subprocess.SubprocessFlow.saved
    prepare = test_planning.NativeJointFlow.prepare
    draft = test_planning.JointFlow.draft
    new_run_engine_args = test_planning.NativeJointFlow.new_run_engine_args

    def test_named_resume_settles_a_pre_upgrade_checkpoint_without_another_provider(self):
        run, state = self.draft()
        self.launch(["--run-dir", str(run), "--approve-goal", state["displayed_goal"]], 0)
        _, state = self.saved()
        # Seed the legacy checkpoint format. All observations and actions after
        # this setup use the public CLI; the runtime hash models an upgrade.
        revision = source_scope.snapshot(self.project, state, base_snapshot=support.snapshot)["revision"]
        contract = state["goal_contract"]["hash"]
        state.update(next_stage="tester", current_task={"id": "legacy-validation-3", "kind": "validate"})
        state["validation"] = {"source_revision": revision, "contract_hash": contract, "output": "legacy-v2"}
        state["findings_ledger"] = [{"id": "F-1", "source": "tester", "status": "open", "blocking": True}]
        frontier = {
            "contract_hash": contract,
            "source_revision": revision,
            "findings": {"F-1": ["reported", None]},
            "passed": [],
            "accepted": [],
        }
        state["validation_only_rounds"] = [
            {
                "attempt": {"task_id": f"legacy-validation-{i}", "after_validation": f"legacy-v{i}"},
                "frontier": deepcopy(frontier),
                "at": "before-upgrade",
            }
            for i in (0, 1)
        ]
        binding = {**human._binding(state), "runtime_sources": "previous-runtime"}
        request = {
            "kind": "blocker",
            "finding_ids": ["F-1"],
            "decision_needed": "Old runner asks the person to repair validation bookkeeping",
            "impact": "Validation made no progress",
        }
        with patch.object(human, "_binding", return_value=binding):
            self.assertTrue(
                runner.resolver_runtime.record_operational_exhaustion(
                    runner, state, run, support.Paused("PAUSED_RESOLVER", "Validation exhausted"), request=request
                )
            )
            self.assertEqual("escalate", human.evaluate(state))
        # Write historical bytes without today's writer normalizing the obsolete
        # runtime token; that upgrade is exactly what the public action exercises.
        (run / "state.json").write_text(json.dumps(state))

        before = json.loads(self.launch(["--run-dir", str(run), "--status"], 0).stdout)["view"]
        self.assertEqual("continue", before["needs"]["kind"], before["needs"])
        self.assertIsNone(before["chat"]["gate"])
        self.assertIsNone(before["chat"]["input"])
        self.assertIn("settle the retained validation limit", before["stop_reason"])
        self.assertNotIn("Old runner asks", before["stop_reason"])
        self.assertIn("automatically", before["explanation"]["what_it_means"])
        self.assertNotIn("--answer", before["explanation"]["explanation"])
        self.assertIn("--resume-paused", before["next_command"])
        resumed = self.launch(shlex.split(before["next_command"])[1:], 2)
        after = json.loads(self.launch(["--run-dir", str(run), "--status"], 0).stdout)["view"]
        self.assertIn(after["stop_reason"], resumed.stdout)
        outcome = after["recovery"]["outcome"]
        self.assertEqual("could_not_finish", outcome["result"])
        self.assertEqual(revision, outcome["evidence"]["source_revision"])
        self.assertEqual(["F-1"], outcome["evidence"]["finding_ids"])
        self.assertEqual(2, outcome["evidence"]["rounds_without_progress"])
        self.assertFalse(after["done"])
        self.assertIsNone(after["next_command"])
        self.assertEqual(before["usage"]["accounting"]["attempts"], after["usage"]["accounting"]["attempts"])
        self.assertEqual(before["routes"], after["routes"])
        self.assertEqual(before["evidence"]["findings"], after["evidence"]["findings"])
        self.launch(["--run-dir", str(run), "--resume-paused", "--no-chat"], 2)
        repeated = json.loads(self.launch(["--run-dir", str(run), "--status"], 0).stdout)["view"]
        self.assertEqual(outcome, repeated["recovery"]["outcome"])
        self.assertEqual(after["usage"]["accounting"]["attempts"], repeated["usage"]["accounting"]["attempts"])
