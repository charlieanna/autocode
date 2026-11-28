"""The actual isolated command runner must work before a Builder is admitted."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_task_preflight as preflight
import autocode_util as util
import autocode_verify as verify

from . import test_task_preflight


class BuilderPreflightTests(unittest.TestCase):
    setUp = test_task_preflight.PreflightFixture.setUp

    def state(self):
        return {
            "next_stage": "builder",
            "settings": {"limits": {"tool_timeout_seconds": 30}},
        }

    def guard(self, state, **worker):
        return preflight.guard(
            state,
            self.workspace,
            self.run_dir,
            worker={"role": "builder", "planning": False, "engine": "codex", "provider": "codex", **worker},
        )

    def test_no_manifest_still_runs_actual_isolated_command_before_builder(self):
        state = self.state()
        with patch.object(verify, "scratch_run", wraps=verify.scratch_run) as execute:
            self.guard(state)
        execute.assert_called_once()
        result = state["task_preflight"]
        self.assertEqual(("prerequisite", "READY", "build"), (result["kind"], result["status"], result["phase"]))
        self.assertEqual("isolated_runner", result["execution_context"])
        self.assertEqual(0, result["checks"][0]["exit_code"])
        self.assertEqual(util.file_hash(result["receipt"]), result["receipt_sha256"])
        self.assertNotIn("regression_proof", state)
        self.assertEqual([], list(self.workspace.glob("runner-probe*")))

    def test_failed_or_empty_probe_is_retained_and_blocks_before_builder(self):
        for receipt in (
            {"exit_code": 1, "tail": "nested runner denied", "timed_out": False},
            {"exit_code": 0, "tail": "", "timed_out": False},
            {"exit_code": 0, "tail": "AUTOCODE_RUNNER_READY", "timed_out": True},
        ):
            with self.subTest(receipt=receipt):
                state = self.state()
                with patch.object(verify, "scratch_run", return_value=receipt):
                    with self.assertRaisesRegex(util.Paused, "before Builder"):
                        self.guard(state)
                saved = json.loads(Path(state["task_preflight"]["receipt"]).read_text())
                self.assertEqual("BLOCKED", saved["status"])
                self.assertEqual(receipt, saved["checks"][0])

    def test_planning_and_report_only_repairs_do_not_launch_runner_checks(self):
        for stage, planning in (("requirements_gather", True), ("builder", True), ("tester", False)):
            with self.subTest(stage=stage, planning=planning), patch.object(verify, "scratch_run") as execute:
                self.guard({**self.state(), "next_stage": stage}, planning=planning)
                execute.assert_not_called()

    def test_uncertain_ownership_is_not_converted_to_a_safe_retry(self):
        state = self.state()
        error = verify.command_receipt.OwnershipUncertain("keeper still owns a command")
        with patch.object(verify, "scratch_run", side_effect=error):
            with self.assertRaises(verify.command_receipt.OwnershipUncertain) as raised:
                self.guard(state)
        self.assertIs(error, raised.exception)

    def test_pending_pause_prevents_even_model_free_runner_admission(self):
        self.run_dir.mkdir(parents=True)
        (self.run_dir / "pause-requested").touch()
        with patch.object(verify, "scratch_run") as execute:
            with self.assertRaises(util.Paused) as stopped:
                self.guard(self.state())
        self.assertEqual("PAUSED_REQUESTED", stopped.exception.status)
        execute.assert_not_called()
