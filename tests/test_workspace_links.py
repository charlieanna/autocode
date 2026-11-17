"""What a workspace's links lead to outside it, and which of those a contained stage may read."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_workspace_links as workspace_links


class ReadTargetTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.workspace = self.root / "project"
        self.shared = self.root / "shared"
        (self.workspace / "src").mkdir(parents=True)
        self.shared.mkdir()
        (self.shared / "discount.py").write_text("RATE = 1\n")
        (self.shared / "lib").mkdir()
        (self.shared / ".env").write_text("TOKEN=withheld\n")
        (self.workspace / "src" / "inside.py").write_text("x = 1\n")

    def link(self, name, target):
        path = self.workspace / name
        path.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(target, path)

    def test_a_link_out_of_the_workspace_grants_reading_its_target_and_nothing_wider(self):
        # Owner decision 2026-11-17: under the macOS sandbox a project whose code is a link out of it could not
        # load its own tests. What its links lead to is readable; the home directory, / and secrets are not.
        self.link("src/discount.py", self.shared / "discount.py")
        self.link("vendor", self.shared / "lib")
        self.link("src/alias.py", "inside.py")
        self.link("config/.env", self.shared / ".env")
        self.link("home", Path.home())
        self.link("everything", "/")
        self.link("gone.py", self.shared / "missing.py")
        subprocess.run(["git", "init", "-q", str(self.workspace)], check=True)
        self.assertEqual(
            [self.shared / "discount.py", self.shared / "lib"],
            workspace_links.read_targets(self.workspace),
        )

    def test_an_ignored_link_grants_nothing(self):
        self.link("build/out.py", self.shared / "discount.py")
        (self.workspace / ".gitignore").write_text("build/\n")
        subprocess.run(["git", "init", "-q", str(self.workspace)], check=True)
        self.assertEqual([], workspace_links.read_targets(self.workspace))

    def test_a_home_directory_below_the_target_is_never_granted(self):
        self.link("data", self.shared)
        with mock.patch.object(workspace_links.Path, "home", return_value=self.shared / "lib"):
            self.assertEqual([], workspace_links.read_targets(self.workspace))


if __name__ == "__main__":
    unittest.main()
