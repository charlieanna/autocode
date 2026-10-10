"""Private token transport through the real public CLI, without any model."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from autocode_taskrun import TaskRun, TaskRunError

CLI = Path(__file__).resolve().parents[1] / "tools" / "autocode.py"


class AuthorizationCliTests(unittest.TestCase):
    def test_public_actions_refuse_missing_runs_without_exposing_tokens_in_argv(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            run = TaskRun(
                root,
                root / ".autocode/runs/never-created",
                command=(sys.executable, "-B", str(CLI)),
                env={"AUTOCODE_HOME": str(root / "registry"), "PYTHONDONTWRITEBYTECODE": "1"},
                timeout=45,
            )
            canary = "NON_AUTHORIZING_CLI_" + os.urandom(12).hex()
            for action in (
                lambda: run.approve_plan(canary),
                lambda: run.approve_review("C1", canary),
                lambda: run.answer("Q1", "No action authorized", resolver_token=canary),
                lambda: run.recover_job_report(canary),
                lambda: run.retry_job(canary),
            ):
                with self.subTest(action=action), self.assertRaises(TaskRunError) as caught:
                    action()
                result = caught.exception.process
                self.assertIsNotNone(result)
                self.assertEqual(2, result.returncode)
                self.assertIn("@stdin", result.args)
                self.assertIn("--authorization-stdin", result.args)
                self.assertNotIn(canary, " ".join(result.args))
                self.assertIn("state.json", result.stderr)
            self.assertFalse((run.run_dir / "state.json").exists())

    def test_malformed_private_input_is_refused_before_run_lookup(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(CLI),
                    "--workspace",
                    directory,
                    "--run-dir",
                    directory + "/missing",
                    "--approve-goal",
                    "@stdin",
                    "--authorization-stdin",
                ],
                input='{"schema":1,"tokens":{"approve_goal":"\\ud800"}}',
                capture_output=True,
                text=True,
                timeout=45,
            )
            self.assertEqual(2, result.returncode)
            self.assertIn("Authorization input: invalid token value", result.stderr)
            self.assertNotIn("state.json", result.stderr)
            self.assertNotIn("Traceback", result.stderr)
