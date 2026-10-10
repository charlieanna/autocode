"""Offline demo through the public CLI, including refusal controls and evidence."""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_demo as demo

ROOT = Path(__file__).resolve().parents[1]


class DemoTests(unittest.TestCase):
    def test_help_does_not_create_files_or_start_a_run(self):
        with patch.object(demo.tempfile, "mkdtemp") as create, patch.object(demo.TaskRun, "start") as start:
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as result:
                    demo.cli(["--help"])
            self.assertEqual(0, result.exception.code)
            create.assert_not_called()
            start.assert_not_called()

    def test_existing_destination_is_refused_and_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "keep.txt"
            marker.write_text("existing user's file")
            with patch.object(demo.TaskRun, "start") as start, contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(1, demo.cli(["--directory", directory]))
            self.assertEqual("existing user's file", marker.read_text())
            self.assertEqual([marker], list(Path(directory).iterdir()))
            start.assert_not_called()

    def test_offline_cli_refuses_false_evidence_and_completes_after_acceptance(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "demo with spaces"
            sentinel = "demo-must-not-inherit-this-provider-secret-689"
            environment = dict(
                os.environ,
                AUTOCODE_PROVIDER="nonexistent-provider-for-demo-control",
                AUTOCODE_FIXTURE_MODE="stalled",
                AUTOCODE_FIXTURE_QUOTA_STAGE="astra_discovery",
                AUTOCODE_APPROVE_GOAL_TOKEN="unrelated-approval",
                OPENAI_API_KEY=sentinel,
                GIT_DIR="/nonexistent-demo-git-control",
            )
            result = subprocess.run(
                [sys.executable, "-B", str(ROOT / "tools/autocode.py"), "demo", "--directory", str(target)],
                env=environment,
                capture_output=True,
                text=True,
                timeout=720,
            )
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            record = json.loads((target / "demo.json").read_text())
            self.assertEqual("fake", record["provenance"])
            refused = json.loads((target / "refused-check.json").read_text())
            self.assertFalse(refused["done"])
            self.assertIn("was reported as exit 0", refused["stop_reason"])
            self.assertIn("exited 1", refused["stop_reason"])
            pending = json.loads((target / "refused-completion.json").read_text())
            self.assertFalse(pending["done"])
            self.assertEqual("review", pending["needs"]["kind"])
            self.assertEqual("PASS", pending["evidence"]["check_replay"]["verdict"])
            self.assertTrue(all(row["status"] == "verified" for row in pending["evidence"]["acceptance"]))
            completed = json.loads((target / "completed.json").read_text())
            self.assertTrue(completed["done"])
            self.assertEqual("current", completed["evidence_report"]["availability"])
            self.assertTrue(all(row["human_reviewed"] for row in completed["evidence"]["acceptance"]))
            self.assertEqual([0], [row["exit_code"] for row in completed["evidence"]["check_replay"]["checks"]])
            for run_key in ("refused_check_run", "completed_run"):
                self.assertTrue(Path(record[run_key]).is_relative_to(target.resolve()))
            self.assertIn(str(record["completed_run"]), result.stdout)
            self.assertIn("Fake results", result.stdout)
            for name in ("greeting-environment", "refused-check-environment"):
                launcher = (target / name / "launch.py").read_text()
                self.assertNotIn(sentinel, launcher)
                self.assertNotIn("nonexistent-provider-for-demo-control", launcher)
            greeting = Path(record["completed_run"]).parents[2] / "greet.py"
            for name, code, output in (("Ada", 0, "Hello, Ada\n"), ("", 2, ""), ("   ", 2, "")):
                check = subprocess.run([sys.executable, str(greeting), name], capture_output=True, text=True)
                self.assertEqual((code, output), (check.returncode, check.stdout))


if __name__ == "__main__":
    unittest.main()
