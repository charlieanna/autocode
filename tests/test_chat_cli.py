"""Drive the chat projection through the public CLI with the offline provider."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import autocode_taskrun as taskrun
from harness.conformance import ConformanceDriver

from tests.test_taskrun import BRIEF, FIXTURE_OPTIONS, HERE


class ChatCliTests(unittest.TestCase):
    def setUp(self):
        root = Path(tempfile.mkdtemp(prefix="chat-cli-", dir=os.environ.get("BUILD_AUDIT_ARTIFACTS")))
        if not os.environ.get("BUILD_AUDIT_ARTIFACTS"):
            self.addCleanup(shutil.rmtree, root)
        self.workspace = root / "project"
        self.workspace.mkdir()
        subprocess.run(["git", "init", "-q", str(self.workspace)], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(self.workspace),
                "-c",
                "user.name=T",
                "-c",
                "user.email=t@example.test",
                "commit",
                "-q",
                "--allow-empty",
                "-m",
                "base",
            ],
            check=True,
        )
        bindir = root / "bin"
        bindir.mkdir()
        shutil.copy2(HERE / "live_fixture_provider.py", bindir / "codex")
        (bindir / "codex").chmod(0o755)
        self.env = {
            "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
            "AUTOCODE_HOME": str(root / "registry"),
            "PYTHONDONTWRITEBYTECODE": "1",
        }

    def test_chat_named_feedback_and_followup_keep_one_run_and_require_fresh_approval(self):
        run = taskrun.TaskRun.start(
            self.workspace, BRIEF, options=(*FIXTURE_OPTIONS, "--no-adaptive-planning"), env=self.env, timeout=300
        )
        first = run.status()

        def send(view, text):
            client = object.__new__(ConformanceDriver)
            client.project, client.run_dir = self.workspace, run.run_dir
            words, env, source = client.message_command(view, text)
            self.assertEqual("chat.input.next_command", source)
            proc = subprocess.run(
                [*taskrun.AUTOCODE, *words, "--workspace", str(self.workspace), "--run-dir", str(run.run_dir)],
                env={**os.environ, **self.env, **env},
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
            return run.status()

        self.assertEqual("feedback", first["chat"]["input"]["kind"])
        # The offline provider has one fixed requirements corpus. Reiterate a
        # supported clause to exercise invalidation without inventing model output.
        feedback = "Python standard library only."
        saved = send(first, feedback)
        self.assertEqual(first["usage"], saved["usage"], "Saving feedback must not launch a provider")
        self.assertIn(feedback, [row["text"] for row in saved["chat"]["messages"]])
        self.assertFalse((self.workspace / "greet.py").exists())
        refreshed = run.advance_until_input()
        self.assertEqual("approve_plan", refreshed["needs"]["kind"])
        with self.assertRaises(taskrun.TaskRunError):
            run.approve_plan(first["needs"]["token"])
        run.approve_plan(refreshed["needs"]["token"])
        finished = run.advance_until_input()
        self.assertEqual("TASK_COMPLETE", finished["status"])
        reply = finished["chat"]["messages"][-1]
        self.assertEqual("assistant", reply["role"])
        self.assertEqual("follow_up", finished["chat"]["input"]["kind"])
        followup = "Review the greeting implementation."
        saved = send(finished, followup)
        self.assertEqual(2, saved["turn"])
        self.assertFalse(saved["done"])
        self.assertIn(followup, [row["text"] for row in saved["chat"]["messages"]])
        self.assertIn(reply["text"], [row["text"] for row in saved["chat"]["messages"] if row["role"] == "assistant"])
        reattached = taskrun.TaskRun(self.workspace, run.run_dir, env=self.env)
        self.assertEqual(saved["chat"], reattached.status()["chat"])
