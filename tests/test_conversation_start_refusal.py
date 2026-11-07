"""A refused conversation start leaves at most one project per task in the person's folder (#1957)."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TASK = "Build wordcount.py, a command-line tool"
# Refused by the launch's own argument parsing, after the project is bootstrapped and
# before a run or any provider request exists.
REFUSED = ("--engine", "not-an-engine")


class RefusedConversationStartTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="autocode-refused-start-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.folder = self.root / "folder"
        self.folder.mkdir()
        (self.folder / "README.md").write_text("notes\n")
        self.env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        self.env["AUTOCODE_HOME"] = str(self.root / "home")

    def start(self, request_id, text=TASK):
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "tools" / "autocode.py"),
                "conversation",
                "start",
                "--workspace",
                str(self.folder),
                "--text",
                text,
                "--request-id",
                request_id,
                "--",
                *REFUSED,
            ],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertIn("invalid choice", result.stderr)
        return result.stderr

    def projects(self):
        return sorted((self.folder / "autocode-projects").iterdir())

    def test_retries_reuse_the_project_the_refused_start_created(self):
        first = self.start("attempt-1")
        [project] = self.projects()
        self.assertIn(str(project), first)

        # A new request for the same task, and the same request again, both relaunch
        # in that project instead of minting another or reporting an unresolved start.
        self.assertIn(str(project), self.start("attempt-2"))
        self.assertIn(str(project), self.start("attempt-1"))
        self.assertEqual([project], self.projects())
        self.assertEqual({".autocode", "README.md", "autocode-projects"}, {p.name for p in self.folder.iterdir()})

    def test_a_project_with_the_persons_changes_or_another_task_is_not_reused(self):
        self.start("attempt-1")
        [project] = self.projects()
        self.start("other-task", text="Build a different tool")
        self.assertEqual(2, len(self.projects()))

        (project / "notes.txt").write_text("mine\n")
        self.start("attempt-2")
        self.assertEqual(3, len(self.projects()))
        self.assertEqual("mine\n", (project / "notes.txt").read_text())


if __name__ == "__main__":
    unittest.main()
