"""The Git checkouts a folder outside Git holds (autocode_folder_checkouts, #2066)."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import autocode_folder_checkouts as folder_checkouts


class FoundTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="folder-checkouts-")
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()

    def checkout(self, *parts: str, worktree=False) -> Path:
        path = self.root.joinpath(*parts)
        path.mkdir(parents=True)
        if worktree:
            (path / ".git").write_text("gitdir: /elsewhere\n")
        else:
            (path / ".git").mkdir()
        return path

    def test_children_then_grandchildren_and_never_inside_a_checkout_or_past_the_depth(self):
        api = self.checkout("api")
        linked = self.checkout("zeta", worktree=True)
        history = self.checkout("group", "history")
        self.checkout("api", "vendored", "inner")  # inside a checkout: not looked at
        self.checkout("a", "b", "too-deep")  # three levels down
        (self.root / "node_modules" / "pkg").mkdir(parents=True)
        ((self.root / "node_modules" / "pkg") / ".git").mkdir()
        (self.root / "README").write_text("not a folder")
        os.symlink(api, self.root / "link")
        self.assertEqual([api, linked, history], folder_checkouts.found(self.root))
        self.assertEqual([api, linked], folder_checkouts.found(self.root, depth=1))

    def test_the_scan_is_bounded(self):
        api = self.checkout("api")
        self.checkout("web")
        self.assertEqual([api], folder_checkouts.found(self.root, budget=1))
        self.assertEqual([], folder_checkouts.found(self.root / "missing"))

    def test_names_the_first_three_and_counts_the_rest(self):
        self.assertEqual("a", folder_checkouts.names([Path("/x/a")]))
        self.assertEqual("a, b, c", folder_checkouts.names(["/x/a", "/x/b", "/x/c"]))
        self.assertEqual("a, b, c and 2 more", folder_checkouts.names(["/x/a", "/x/b", "/x/c", "/x/d", "/x/e"]))


if __name__ == "__main__":
    unittest.main()
