"""A JSON status observer must not stop a fake-provider CLI launch."""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from tests.test_agent_step import fixture_workspace
from tests.test_taskrun import BRIEF, FIXTURE_OPTIONS


@unittest.skipUnless(sys.platform == "linux", "supervised provider requires supported Linux")
class JsonStatusObserverCliTests(unittest.TestCase):
    def test_reader_allows_requirements_launch_and_saved_reopen(self):
        workspace, env = fixture_workspace(self)
        workspace = workspace.resolve()
        run = workspace / ".autocode/runs/json-observer"
        # Hold the exact observer argv while the real CLI starts. The handshake
        # ensures the process is visible before admission checks, without sleeps.
        observer = workspace.parent / "observer/autocode.py"
        observer.parent.mkdir()
        observer.write_text("import sys\nprint('ready', flush=True)\nsys.stdin.read()\n")
        paths = ["--workspace", str(workspace), "--run-dir", str(run)]
        process = subprocess.Popen(
            [sys.executable, str(observer), "--status", *paths, "--json"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        self.addCleanup(process.communicate, "", timeout=30)
        self.assertEqual("ready\n", process.stdout.readline())
        environment = {**os.environ, **env}
        environment["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env["PATH"]
        cli = [sys.executable, str(Path(__file__).resolve().parents[1] / "tools/autocode.py")]
        started = subprocess.run(
            [
                *cli,
                BRIEF,
                "--workspace",
                str(workspace),
                "--new-run-dir",
                str(run),
                "--in-place",
                *FIXTURE_OPTIONS,
                "--no-chat",
                "--pause-after-stage",
                "--json",
            ],
            env=environment,
            capture_output=True,
            text=True,
            timeout=180,
        )
        self.assertNotIn("Existing run process", started.stderr)
        self.assertTrue(started.stdout.strip(), started.stderr)
        view = json.loads(started.stdout)["view"]
        self.assertEqual("PAUSED_REQUESTED", view["status"], started.stderr)
        self.assertGreater(view["cost"]["breakdown"]["by_stage"][0]["stages"], 0)
        reopened = subprocess.run(
            [*cli, "--status", *paths, "--json"],
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        saved = json.loads(reopened.stdout)["view"]
        self.assertEqual(view["status"], saved["status"])
        self.assertEqual(view["cost"], saved["cost"])
