"""The planning-call cap in the status view of a new run (owner decision 2026-11-12). The pause itself, a plain
resume that holds and removing the cap are driven through the CLI in tests.test_planning (JointFlow)."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import autocode_taskrun as taskrun

TOOLS = Path(__file__).resolve().parents[1] / "tools"
BRIEF = "Build greeting and goodbye"
OPTIONS = ("--evidence-provenance", "fake", "--engine", "codex")


class PlanningCallCapCliTests(unittest.TestCase):
    """Through the real CLI and TaskRun with the scripted Codex fixture."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="planning-calls-")
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.workspace = root / "project"
        self.workspace.mkdir()
        git = ["git", "-C", str(self.workspace), "-c", "user.name=T", "-c", "user.email=t@example.test"]
        subprocess.run(["git", "init", "-q", str(self.workspace)], check=True)
        subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "base"], check=True)
        bindir = root / "bin"
        bindir.mkdir()
        shutil.copy2(TOOLS / "fake_codex.py", bindir / "codex")
        shutil.copy2(TOOLS / "goal_fixtures.py", bindir / "goal_fixtures.py")
        (bindir / "codex").chmod(0o755)
        self.env = {
            "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
            "AUTOCODE_HOME": str(root / "registry"),
            "XDG_CONFIG_HOME": str(root / "config"),
            "CODEX_HOME": str(root / "codex-home"),
            "AUTOCODE_FIXTURE_MODE": "milestones",
            "PYTHONDONTWRITEBYTECODE": "1",
        }

    def test_a_new_run_shows_the_default_cap_of_twenty(self):
        started = taskrun.TaskRun.start(self.workspace, BRIEF, options=OPTIONS, env=self.env, timeout=300)
        view = started.status()
        self.assertEqual(20, view["planning_calls"]["cap"])
        self.assertGreaterEqual(view["planning_calls"]["used"], 1)


if __name__ == "__main__":
    unittest.main()
