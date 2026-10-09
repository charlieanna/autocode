"""Scratch worktrees must remain reusable after probes create read-only caches."""

import unittest
from unittest.mock import patch

import autocode_verify as verify

from tests.test_verify import Project, git


class ScratchCleanupTests(unittest.TestCase):
    def setUp(self):
        self.project = Project()
        self.addCleanup(self.project.close)
        self.tree = self.project.evidence / "scratch" / "tree"

    def readonly_cache(self, tree):
        cache = tree / ".gopath" / "pkg" / "mod" / "example.test" / "lib@v1"
        cache.mkdir(parents=True)
        (cache / "value.go").write_text("package lib\n")
        (cache / "value.go").chmod(0o444)
        cache.chmod(0o555)
        return cache

    def test_remove_tree_removes_readonly_probe_cache(self):
        verify.make_tree(self.project.root, self.project.base, self.tree, self.project.root, {})
        cache = self.readonly_cache(self.tree)
        self.addCleanup(lambda: cache.chmod(0o755) if cache.exists() else None)

        verify.remove_tree(self.project.root, self.tree)

        self.assertFalse(self.tree.exists())
        self.assertEqual(1, len(git(self.project.root, "worktree", "list").splitlines()))
        self.assertEqual({}, verify.changed_files(self.project.root, self.project.base))

    def test_probe_can_retry_after_previous_readonly_cache(self):
        verify.make_tree(self.project.root, self.project.base, self.tree, self.project.root, {})
        cache = self.readonly_cache(self.tree)
        self.addCleanup(lambda: cache.chmod(0o755) if cache.exists() else None)

        receipt = verify.scratch_run(self.project.root, self.project.evidence, command="git rev-parse HEAD")

        self.assertEqual(0, receipt["exit_code"])
        self.assertEqual(self.project.base, receipt["tail"].strip())
        self.assertFalse(self.tree.exists())

    def test_cleanup_does_not_follow_dependency_symlink(self):
        external = self.project.root / "ignored-dependency"
        external.mkdir()
        resource = external / "keep.txt"
        resource.write_text("retain me")
        resource.chmod(0o444)
        external.chmod(0o555)
        self.addCleanup(external.chmod, 0o755)
        verify.make_tree(self.project.root, self.project.base, self.tree, self.project.root, {})
        (self.tree / ".venv").symlink_to(external, target_is_directory=True)
        cache = self.readonly_cache(self.tree)
        self.addCleanup(lambda: cache.chmod(0o755) if cache.exists() else None)

        verify.remove_tree(self.project.root, self.tree)

        self.assertFalse(self.tree.exists())
        self.assertEqual("retain me", resource.read_text())
        self.assertEqual(0o555, external.stat().st_mode & 0o777)
        self.assertEqual(0o444, resource.stat().st_mode & 0o777)

    def test_cleanup_reports_unrecoverable_failure(self):
        self.tree.mkdir(parents=True)
        cache = self.readonly_cache(self.tree)
        self.addCleanup(lambda: cache.chmod(0o755) if cache.exists() else None)
        error = OSError("unavailable filesystem")
        with patch.object(verify.os, "chmod", side_effect=error):
            with self.assertRaisesRegex(OSError, "unavailable filesystem"):
                verify.remove_tree(self.project.root, self.tree)

    def test_cleanup_unlinks_root_symlink_without_changing_target(self):
        external = self.project.root / "external"
        external.mkdir()
        (external / "keep.txt").write_text("retain me")
        self.tree.parent.mkdir(parents=True)
        self.tree.symlink_to(external, target_is_directory=True)

        verify.remove_tree(self.project.root, self.tree)

        self.assertFalse(self.tree.is_symlink())
        self.assertEqual("retain me", (external / "keep.txt").read_text())


if __name__ == "__main__":
    unittest.main()
