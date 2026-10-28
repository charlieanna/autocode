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

    def test_a_second_merge_reports_the_branch_as_already_merged(self):
        """#2315: the first merge moved the base to the run's own commit, and the second merge read that
        as "the base branch moved after this run started; re-validate ... then merge again"."""
        meta = workspaces.create(self.project, "Build a greeting CLI")
        tree = Path(meta["workspace"])
        (tree / "b.txt").write_text("two\n")
        git(tree, "add", "b.txt")
        git(tree, "-c", "user.name=T", "-c", "user.email=t@e.test", "commit", "-qm", "deliver")
        self.assertTrue(merge_mod.merge(self.project, tree)[0])
        ok, message = merge_mod.merge(self.project, tree)
        self.assertTrue(ok, message)
        self.assertIn("already merged: master at", message)
        self.assertNotIn("re-validate", message)

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

    def test_merge_cli_json_success(self):
        meta = workspaces.create(self.project, "Build a greeting CLI")
        tree = Path(meta["workspace"])
        (tree / "b.txt").write_text("two\n")
        git(tree, "add", "b.txt")
        git(tree, "-c", "user.name=T", "-c", "user.email=t@e.test", "commit", "-qm", "deliver")
        import contextlib
        import io
        import json

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = merge_mod.cli(["--workspace", str(self.project), str(tree), "--json"])
        self.assertEqual(0, code)
        payload = json.loads(out.getvalue())
        self.assertTrue(payload["ok"])
        self.assertIn("fast-forwarded", payload["message"])
        self.assertEqual(str(self.project.resolve()), str(Path(payload["project"]).resolve()))
        self.assertEqual(str(tree.resolve()), str(Path(payload["worktree"]).resolve()))

    def test_merge_cli_json_refused(self):
        meta = workspaces.create(self.project, "Build a greeting CLI")
        tree = Path(meta["workspace"])
        (tree / "b.txt").write_text("two\n")
        import contextlib
        import io
        import json

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = merge_mod.cli(["--workspace", str(self.project), str(tree), "--json"])
        self.assertEqual(1, code)
        payload = json.loads(out.getvalue())
        self.assertFalse(payload["ok"])
        self.assertTrue("branch" in payload["message"].lower() or "delivered" in payload["message"].lower())

    def test_merge_without_a_worktree_argument_says_how_many_there_are(self):
        import contextlib
        import io

        def refusal(argv):
            err = io.StringIO()
            with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
                merge_mod.cli(argv)
            return err.getvalue()

        self.assertIn("no task worktree under", refusal(["--workspace", str(self.project)]))
        first = Path(workspaces.create(self.project, "Build a greeting CLI")["workspace"])
        second = Path(workspaces.create(self.project, "Build a farewell CLI")["workspace"])
        message = refusal(["--workspace", str(self.project)])
        self.assertIn("this project has 2:", message)
        self.assertIn(first.name, message)
        self.assertIn(second.name, message)


if __name__ == "__main__":
    unittest.main()
