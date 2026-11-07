"""Unsupported native captures refuse at export without replaying a stage."""

import hashlib
import json
import subprocess
import sys
import unittest

from . import test_codex_capture_copy as fixture


class NativeCaptureExportCLI(unittest.TestCase):
    run_to_validation = fixture.CodexCaptureWorkflowTests.run_to_validation

    def setUp(self):
        fixture.CodexCaptureWorkflowTests.setUp(self)
        self.options = (*self.options, "--max-cost-usd", "0", "--max-parallel-builders", "1")

    def test_export_and_direct_replay_refuse_before_creating_unusable_destinations(self):
        run, view = self.run_to_validation()
        self.assertEqual("TASK_COMPLETE", view["status"], view)
        receipt = self.receipt.read_bytes()
        self.assertIn("verification_copy", json.loads(receipt))
        launches = self.probe.read_bytes()
        original = {
            str(path.relative_to(run.run_dir)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in run.run_dir.rglob("*")
            if path.is_file() and not path.is_symlink()
        }
        export = self.root / "recording"
        replay_workspace = self.root / "replayed-project"
        for option, destination in (
            ("--export", export),
            ("--into", replay_workspace / ".autocode/runs/replay"),
        ):
            with self.subTest(option=option):
                result = subprocess.run(
                    [
                        sys.executable,
                        str(fixture.HERE / "autocode.py"),
                        "replay",
                        str(run.run_dir),
                        option,
                        str(destination),
                        "--json",
                    ],
                    cwd=self.workspace,
                    env=self.env,
                    capture_output=True,
                    text=True,
                    timeout=45,
                )
                self.assertEqual(3, result.returncode, result.stdout + result.stderr)
                refusal = json.loads(result.stderr)
                self.assertEqual("REPLAY_REFUSED", refusal["code"])
                self.assertIn("native capture receipt dependencies are unsupported in v1", refusal["message"])
                self.assertFalse(destination.exists())
        self.assertFalse(export.exists())
        self.assertFalse(replay_workspace.exists(), "refusal started a replay workspace")
        self.assertEqual(launches, self.probe.read_bytes(), "refusal launched another stage")
        self.assertEqual(receipt, self.receipt.read_bytes())
        self.assertEqual(
            original,
            {
                str(path.relative_to(run.run_dir)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in run.run_dir.rglob("*")
                if path.is_file() and not path.is_symlink()
            },
        )
