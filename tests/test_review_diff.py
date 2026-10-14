"""Only publicly named, bounded ordinary files contribute terminal review diffs."""

import copy
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_review_diff as review_diff
import autocode_review_presentation as presentation


class ReviewDiffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir()
        self.git("init", "-q")
        (self.root / "greet.py").write_text("print('old')\n")
        (self.root / "deleted.py").write_text("old\n")
        (self.root / "untouched.py").write_text("private\n")
        self.git("add", ".")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "base")
        self.base = self.git("rev-parse", "HEAD").strip()
        self.view = {"evidence": {"base_commit": self.base, "diff_coverage": {"files": [{"path": "greet.py"}]}}}

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args], text=True, stderr=subprocess.PIPE)

    def test_actual_hunks_are_read_only_and_limited_to_public_paths(self):
        (self.root / "greet.py").write_text("print('new')\n")
        (self.root / "untouched.py").write_text("unrelated change\n")
        before = copy.deepcopy(self.view)
        status = self.git("status", "--porcelain")
        shown = review_diff.collect(self.root, self.view)
        self.assertEqual("available", shown["availability"])
        self.assertEqual(["greet.py"], [row["path"] for row in shown["files"]])
        self.assertIn("@@ -1 +1 @@", shown["files"][0]["text"])
        self.assertIn("-print('old')\n+print('new')", shown["files"][0]["text"])
        self.assertNotIn("unrelated", str(shown))
        self.assertEqual(status, self.git("status", "--porcelain"))
        self.assertEqual(before, self.view)
        text = presentation.render_review({"id": "C1", "text": "Greeting", "diff_hunks": shown})
        self.assertIn("Run-level diff hunks:", text)
        self.assertIn("not criterion-specific", text)
        self.assertIn("+print('new')", text)

    def test_added_and_deleted_paths_and_missing_newlines_have_explicit_diffs(self):
        (self.root / "added.py").write_text("added without newline")
        (self.root / "deleted.py").unlink()
        self.view["evidence"]["diff_coverage"]["files"] = [{"path": name} for name in ("added.py", "deleted.py")]
        shown = review_diff.collect(self.root, self.view)
        self.assertEqual("available", shown["availability"])
        added, deleted = shown["files"]
        self.assertIn("--- /dev/null", added["text"])
        self.assertIn("+added without newline\n\\ No newline at end of file", added["text"])
        self.assertIn("+++ /dev/null", deleted["text"])

    def test_outside_paths_and_symlinks_are_unavailable_without_reading_targets(self):
        outside = self.root.parent / "outside.txt"
        outside.write_text("DO NOT DISPLAY")
        (self.root / "link.txt").symlink_to(outside)
        (self.root / "linked_dir").symlink_to(self.root.parent, target_is_directory=True)
        names = [
            "../outside.txt",
            str(outside),
            "link.txt",
            "linked_dir/outside.txt",
            ".git/config",
            ".AUTOCODE/state.json",
            ".GIT/config",
        ]
        self.view["evidence"]["diff_coverage"]["files"] = [{"path": name} for name in names]
        shown = review_diff.collect(self.root, self.view)
        self.assertEqual("unavailable", shown["availability"])
        self.assertTrue(all(row["availability"] == "unavailable" for row in shown["files"]))
        self.assertNotIn("DO NOT DISPLAY", str(shown))

    def test_missing_public_scope_or_base_never_falls_back_to_entire_checkout(self):
        for evidence in ({}, {"base_commit": self.base}, {"diff_coverage": {"files": [{"path": "greet.py"}]}}):
            with self.subTest(evidence=evidence), patch.object(review_diff, "_git") as git:
                shown = review_diff.collect(self.root, {"evidence": evidence})
                self.assertEqual("unavailable", shown["availability"])
                self.assertEqual([], shown["files"])
                git.assert_not_called()

    def test_file_size_and_binary_data_are_not_silently_truncated_into_evidence(self):
        for data in (b"x" * (review_diff.MAX_FILE_BYTES + 1), b"binary\x00data", b"\xff"):
            with self.subTest(data_size=len(data)):
                (self.root / "greet.py").write_bytes(data)
                shown = review_diff.collect(self.root, self.view)
                self.assertEqual("unavailable", shown["availability"])
                self.assertIsNone(shown["files"][0]["text"])
                self.assertTrue(shown["files"][0]["reason"])


if __name__ == "__main__":
    unittest.main()
