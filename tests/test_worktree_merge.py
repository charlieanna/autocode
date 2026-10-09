"""Every run in its own worktree; merge delivers it back (#724)."""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
import autocode_merge as merge_mod
import autocode_workspaces as workspaces


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout.strip()


class WorktreeMergeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()
        git(self.project, "init", "-q")
        git(self.project, "config", "user.name", "T")
        git(self.project, "config", "user.email", "t@e.test")
        (self.project / "a.txt").write_text("one\n")
        git(self.project, "add", "a.txt")
        git(self.project, "commit", "-qm", "init")
        self.base = git(self.project, "rev-parse", "HEAD")

    def test_a_new_run_lives_in_its_own_worktree_and_the_checkout_is_untouched(self):
        # The default already exists in run_setup: not --in-place creates a worktree.
        meta = workspaces.create(self.project, "Build a greeting CLI")
        tree = Path(meta["workspace"]).resolve()
        self.assertTrue(tree.is_relative_to((self.project / ".autocode" / "worktrees").resolve()))
        self.assertEqual("one\n", (self.project / "a.txt").read_text())
        self.assertEqual([], git(self.project, "status", "--porcelain").splitlines())
        (tree / "b.txt").write_text("two\n")
        self.assertEqual("one\n", (self.project / "a.txt").read_text())

    def test_merge_fast_forwards_a_delivered_branch(self):
        meta = workspaces.create(self.project, "Build a greeting CLI")
        tree = Path(meta["workspace"])
        (tree / "b.txt").write_text("two\n")
        git(tree, "add", "b.txt")
        git(tree, "-c", "user.name=T", "-c", "user.email=t@e.test", "commit", "-qm", "deliver")
        ok, message = merge_mod.merge(self.project, tree)
        self.assertTrue(ok, message)
        self.assertTrue((self.project / "b.txt").exists())
        self.assertEqual("master", git(self.project, "symbolic-ref", "--short", "HEAD"))
        self.assertIn("fast-forwarded", message)

    def test_a_moved_base_is_refused_with_the_revalidate_instruction(self):
        meta = workspaces.create(self.project, "Build a greeting CLI")
        tree = Path(meta["workspace"])
        (tree / "b.txt").write_text("two\n")
        git(tree, "add", "b.txt")
        git(tree, "-c", "user.name=T", "-c", "user.email=t@e.test", "commit", "-qm", "deliver")
        # Move the base past the run's start.
        (self.project / "c.txt").write_text("three\n")
        git(self.project, "add", "c.txt")
        git(self.project, "-c", "user.name=T", "-c", "user.email=t@e.test", "commit", "-qm", "base moved")
        ok, message = merge_mod.merge(self.project, tree)
        self.assertFalse(ok)
        self.assertIn("re-validate", message)
        self.assertFalse((self.project / "b.txt").exists())

    def test_merge_needs_a_delivered_branch(self):
        meta = workspaces.create(self.project, "Build a greeting CLI")
        tree = Path(meta["workspace"])
        (tree / "b.txt").write_text("two\n")
        ok, message = merge_mod.merge(self.project, tree)
        # No commit on the branch yet: the source is uncommitted, so there is nothing to merge.
        self.assertFalse(ok)
        self.assertTrue("branch" in message.lower() or "delivered" in message.lower(), message)


if __name__ == "__main__":
    unittest.main()
