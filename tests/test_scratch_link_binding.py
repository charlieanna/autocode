"""Pinned scratch copies reject source links whose target bytes are not bound."""

import os
import unittest

import autocode_source_snapshot as source

from tests.test_verify import Project, git


class ScratchLinkBindingTests(unittest.TestCase):
    def test_only_tracked_in_tree_ordinary_targets_are_bound(self):
        project = Project({"src/real.py": "value = 1\n", "src/other.py": "value = 2\n"})
        self.addCleanup(project.close)
        root = project.root
        link = root / "alias.py"
        link.symlink_to("src/real.py")
        git(root, "add", "alias.py")
        git(root, "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "link")
        tracked = lambda: git(root, "ls-files", "-z").split("\0")
        snapshot = source.snapshot(root)["files"]
        check = getattr(source, "unbound_symlinks", None)
        self.assertIsNotNone(check)
        self.assertEqual([], check(root, snapshot, tracked))
        link.unlink()
        link.symlink_to("src/other.py")
        self.assertIn("alias.py", check(root, snapshot, tracked))
        link.unlink()
        link.symlink_to("../outside.py")
        self.assertIn("alias.py", check(root, source.snapshot(root)["files"], tracked))
        link.unlink()
        link.symlink_to("src/real.py")
        os.link(root / "src/real.py", root.parent / "external-hardlink.py")
        self.assertIn("alias.py", check(root, source.snapshot(root)["files"], tracked))
