import os
import tempfile
import unittest
from pathlib import Path

import autocode_variant_patch as variant_patch

SOURCE = "def f(x):\n    return x + 1\n\n\ndef g(x):\n    return x\n"


class VariantPatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workspace = Path(self.tmp.name, "ws")
        (self.workspace / "pkg").mkdir(parents=True)
        (self.workspace / "pkg" / "m.py").write_text(SOURCE)
        self.folder = Path(self.tmp.name, "variant")

    def files(self, patch):
        return variant_patch.files(self.workspace, patch, self.folder)

    def test_a_hand_written_hunk_is_applied_to_a_copy_and_the_workspace_is_unchanged(self):
        # No context lines and a header whose counts are wrong: plain `git apply` refuses this.
        overlay = self.files("--- a/pkg/m.py\n+++ b/pkg/m.py\n@@ -2,9 +2,9 @@\n-    return x + 1\n+    return x + 2\n")
        self.assertEqual(list(overlay), ["pkg/m.py"])
        self.assertIn("return x + 2", Path(overlay["pkg/m.py"]).read_text())
        self.assertEqual((self.workspace / "pkg" / "m.py").read_text(), SOURCE)

    def test_a_new_file_is_part_of_the_overlay(self):
        overlay = self.files("--- /dev/null\n+++ b/pkg/extra.py\n@@ -0,0 +1 @@\n+X = 1\n")
        self.assertEqual(Path(overlay["pkg/extra.py"]).read_text(), "X = 1\n")
        self.assertFalse((self.workspace / "pkg" / "extra.py").exists())

    def test_a_patch_that_does_not_match_says_why(self):
        with self.assertRaisesRegex(ValueError, "patch failed|does not apply|corrupt"):
            self.files("--- a/pkg/m.py\n+++ b/pkg/m.py\n@@ -2 +2 @@\n-    return nothing\n+    return x\n")

    def test_deleting_or_renaming_a_file_is_refused(self):
        with self.assertRaisesRegex(ValueError, "deletes 'pkg/m.py'"):
            self.files(
                "--- a/pkg/m.py\n+++ /dev/null\n@@ -1,6 +0,0 @@\n"
                + "".join(f"-{line}\n" for line in SOURCE.splitlines())
            )
        with self.assertRaisesRegex(ValueError, "deletes 'pkg/m.py'"):
            self.files(
                "diff --git a/pkg/m.py b/pkg/n.py\nsimilarity index 100%\nrename from pkg/m.py\nrename to pkg/n.py\n"
            )

    def test_a_path_outside_the_repository_is_refused(self):
        with self.assertRaisesRegex(ValueError, "not a repository-relative path|outside"):
            self.files("--- a/../escape.py\n+++ b/../escape.py\n@@ -0,0 +1 @@\n+X = 1\n")
        self.assertFalse(Path(self.tmp.name, "escape.py").exists())

    @unittest.skipUnless(hasattr(os, "symlink"), "needs symlinks")
    def test_a_symlinked_file_is_refused(self):
        (self.workspace / "link.py").symlink_to(self.workspace / "pkg" / "m.py")
        with self.assertRaisesRegex(ValueError, "not a regular file"):
            self.files("--- a/link.py\n+++ b/link.py\n@@ -2 +2 @@\n-    return x + 1\n+    return x + 2\n")


if __name__ == "__main__":
    unittest.main()
