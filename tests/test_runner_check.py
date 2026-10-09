"""The CLI must show a resumed run doing local checks before it calls a model."""

import copy
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_command_receipt as command_receipt
import autocode_command_supervision as command_supervision
import autocode_process as processes
import autocode_regression as regression
import autocode_runner_check as runner_check
import autocode_status as status
import autocode_util as util

from tests.test_verify import REFERENCE, Project, isolated_python


class RunnerCheckTests(unittest.TestCase):
    def setUp(self):
        self.python = isolated_python(self)
        self.project = Project()
        self.addCleanup(self.project.close)
        self.run = self.project.root / ".autocode" / "runs" / "fixture"
        self.run.mkdir(parents=True)
        self.path = self.run / "state.json"
        self.state = {
            "version": 3,
            "task": "Check the change",
            "workspace": str(self.project.root),
            "run_dir": str(self.run),
            "status": "RUNNING",
            "iteration": 1,
            "sessions": {},
            "stages": [],
            "next_stage": "sol",
            "base_commit": self.project.base,
            "settings": {"roles": {"sol": {"model": "glm-5.3", "reasoning_effort": "high"}}},
        }
        # Resume has updated memory, but no provider has saved its launch yet.
        self.path.write_text(json.dumps({**self.state, "status": "PAUSED_INTERVENTION"}))

    def cli_status(self):
        before = self.path.read_bytes()
        command = [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "tools" / "autocode.py"),
            "--workspace",
            str(self.project.root),
            "--run-dir",
            str(self.run),
            "--status",
        ]
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        self.assertEqual(before, self.path.read_bytes(), "status must remain read-only")
        return json.loads(result.stdout)

    def test_status_during_baseline_and_comparison_then_clean_handoff(self):
        self.project.write(REFERENCE)
        self.state["settings"]["regression"] = {"python": self.python}
        route = copy.deepcopy(self.state["settings"])
        observed = []
        actual_baseline, actual_verify = regression.verify.baseline, regression.verify.verify

        def observe(label):
            view = self.cli_status()
            observed.append(view["view"]["runner_check"]["summary"])
            self.assertIn(label, observed[-1])
            self.assertEqual("RUNNING", view["status"])
            self.assertEqual("continue", view["view"]["needs"]["kind"])
            self.assertEqual("sol", view["next_stage"])
            self.assertIsNone(view["active_stage"])
            self.assertIsNone(view["active_stage_workers"])
            self.assertFalse(view["stale"])
            self.assertTrue(view["runner_check_workers"]["alive"])
            self.assertEqual([os.getpid()], view["runner_check_workers"]["live_pids"])
            self.assertEqual(route, view["settings"])
            saved = json.loads(self.path.read_text())
            self.assertIn("no model is active", saved["progress_messages"][-1]["text"])

        def baseline(*args, **kwargs):
            observe("original code")
            return actual_baseline(*args, **kwargs)

        def verify(*args, **kwargs):
            observe("candidate suite")
            return actual_verify(*args, **kwargs)

        with (
            patch.object(regression, "required", return_value=True),
            patch.object(regression.verify, "baseline", side_effect=baseline),
            patch.object(regression.verify, "verify", side_effect=verify),
        ):
            regression.before_review(self.state, "sol", self.project.root, self.run)

        self.assertEqual(2, len(observed))
        view = self.cli_status()
        self.assertIsNone(view["view"]["runner_check"])
        self.assertIsNone(view["runner_check_workers"])
        self.assertEqual("PASS", view["view"]["evidence"]["regression_proof"]["verdict"])
        self.assertEqual("sol", view["next_stage"])
        # A cached proof must not publish another active check or repeat the suite.
        with patch.object(regression.verify, "verify") as run_suite:
            regression.prove(self.state, self.project.root, self.run)
        run_suite.assert_not_called()

    def test_exception_clears_activity_without_fabricating_a_proof(self):
        with (
            patch.object(regression, "required", return_value=True),
            patch.object(regression.verify, "baseline", side_effect=RuntimeError("suite failed to start")),
        ):
            with self.assertRaisesRegex(RuntimeError, "suite failed to start"):
                regression.before_review(self.state, "sol", self.project.root, self.run)
        view = self.cli_status()
        self.assertIsNone(view["view"]["runner_check"])
        self.assertIsNone(view["view"]["evidence"]["regression_proof"])

    def test_reused_pid_is_a_stale_check_not_a_live_provider(self):
        with runner_check.track(self.state, self.run, "regression_proof", "Testing the change", status.persist):
            # The PID exists, but its birth identity no longer matches the saved owner.
            saved = json.loads(self.path.read_text())
            owner = saved["active_runner_check"]["processes"][0]
            owner["birth_identity"] -= 1
            self.path.write_text(json.dumps(saved))
            view = self.cli_status()
            self.assertTrue(view["stale"])
            self.assertFalse(view["runner_check_workers"]["alive"])
            self.assertIn("runner check", view["next_action"])
            self.assertIsNone(view["attempt_id"])

    def _hold(self, *, phase="uncertain", cleanup_error="keeper failed"):
        path = self.run / "supervision.json"
        metadata = {
            "schema": 1,
            "nonce": "c" * 32,
            "receipt": str(path.resolve()),
            "owner": {"pid": 301, "birth_identity": 1},
            "keeper": {"pid": 302, "birth_identity": 2},
            "provider": {"pid": 303, "birth_identity": 3},
        }
        util.atomic_json(
            path,
            {
                **metadata,
                "phase": phase,
                "cause": "keeper_failure",
                "cleanup_error": cleanup_error,
                "observed_at": "2026-10-07T03:10:23Z",
                "processes": [metadata["provider"]],
            },
        )
        self.state["active_runner_check"] = {
            "stage": "regression_proof",
            "summary": "baseline",
            "supervision": metadata,
        }
        return metadata

    def _persist(self, path, state):
        path.write_text(json.dumps(state))

    def test_a_dead_uncertain_inventory_is_retired_without_becoming_proof(self):
        self._hold()
        with patch.object(command_supervision.processes, "live_processes", return_value=[]):
            runner_check.clear(self.state, self.run, self._persist)
        self.assertNotIn("active_runner_check", self.state)
        self.assertNotIn("regression_proof", self.state)
        self.assertEqual("runner_check_retired", self.state["user_events"][-1]["kind"])
        saved = json.loads(self.path.read_text()) if self.path.exists() else {}
        self.assertNotIn("active_runner_check", saved)

    def test_a_live_or_unreadable_inventory_keeps_the_hold(self):
        self._hold()
        with patch.object(command_supervision.processes, "live_processes", return_value=[{"pid": 303}]):
            with self.assertRaises(command_receipt.OwnershipUncertain):
                runner_check.clear(self.state, self.run, self._persist)
        self.assertIn("active_runner_check", self.state)
        self.assertNotIn("user_events", self.state)
        (self.run / "supervision.json").write_bytes(b"x" * (command_receipt.MAX_RECEIPT_BYTES + 1))
        with self.assertRaises(command_receipt.OwnershipUncertain):
            runner_check.clear(self.state, self.run, self._persist)
        self.assertIn("active_runner_check", self.state)

    def test_unknown_process_liveness_does_not_claim_a_dead_check(self):
        with runner_check.track(self.state, self.run, "regression_proof", "Testing the change", status.persist):
            with patch.object(processes, "process_table", side_effect=processes.ProcessError("access denied")):
                worker = processes.recorded_worker_state(self.state["active_runner_check"])
            self.assertFalse(worker["checked"])
            self.assertIsNone(worker["alive"])
