"""Only publicly named, bounded ordinary files contribute terminal review diffs."""

import copy
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_criterion_changes as criterion_changes
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


class CriterionDiffTests(unittest.TestCase):
    """Each attributed step is diffed against the step before it, from saved Git objects only."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir()
        self.git("init", "-q")
        self.base = self.commit({"a.py": "a0\n", "b.py": "b0\n"})
        self.first = self.commit({"a.py": "a1\n"})
        self.second = self.commit({"b.py": "b1\n", "new.py": "n\n"})
        (self.root / "a.py").write_text("uncommitted working tree\n")
        self.rows = [
            {
                "id": "code-1",
                "milestone_id": "M1",
                "label": "Change a",
                "changed_files": ["a.py"],
                "commit": self.first,
            },
            {
                "id": "code-2",
                "milestone_id": "M2",
                "label": "Change b",
                "changed_files": ["b.py", "new.py"],
                "commit": self.second,
            },
        ]
        self.body = {
            "acceptance_criteria": [{"id": "C1"}, {"id": "C2"}],
            "milestones": [{"id": "M1", "acceptance_criteria": ["C1"]}, {"id": "M2", "acceptance_criteria": ["C2"]}],
        }

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args], text=True, stderr=subprocess.PIPE)

    def commit(self, files):
        for name, text in files.items():
            (self.root / name).write_text(text)
        self.git("add", ".")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "step")
        return self.git("rev-parse", "HEAD").strip()

    def view(self):
        rows = copy.deepcopy(self.rows)
        return {
            "evidence": {"base_commit": self.base},
            "code_checkpoints": {"rows": rows},
            "changes_by_criterion": criterion_changes.attribute(self.body, rows),
        }

    def test_each_criterion_shows_only_its_own_steps_from_saved_commits(self):
        view = self.view()
        before, status = copy.deepcopy(view), self.git("status", "--porcelain")
        shown = review_diff.criterion_diffs(self.root, view)
        self.assertEqual("available", shown["availability"])
        self.assertEqual(
            [
                {"id": "C1", "checkpoints": ["code-1"], "files": ["a.py"], "overlapping": False},
                {"id": "C2", "checkpoints": ["code-2"], "files": ["b.py", "new.py"], "overlapping": False},
            ],
            shown["criteria"],
        )
        first, second = shown["steps"]["code-1"], shown["steps"]["code-2"]
        self.assertEqual(["a.py"], [row["path"] for row in first["files"]])
        self.assertIn("-a0\n+a1", first["files"][0]["text"])
        self.assertEqual(["b.py", "new.py"], [row["path"] for row in second["files"]])
        self.assertIn("-b0\n+b1", second["files"][0]["text"])
        self.assertIn("--- /dev/null", second["files"][1]["text"])
        self.assertNotIn("a1", str(second))
        self.assertNotIn("uncommitted", str(shown))
        self.assertEqual(status, self.git("status", "--porcelain"))
        self.assertEqual(before, view)

    def test_only_named_criteria_are_read(self):
        shown = review_diff.criterion_diffs(self.root, self.view(), only=["C2"])
        self.assertEqual(["C2"], [row["id"] for row in shown["criteria"]])
        self.assertEqual(["code-2"], list(shown["steps"]))

    def test_a_step_after_an_unavailable_checkpoint_cannot_be_separated(self):
        self.rows[0].update(commit=None, available=False, reason="storage unavailable")
        shown = review_diff.criterion_diffs(self.root, self.view())
        self.assertEqual("unavailable", shown["availability"])
        self.assertIn("storage unavailable", shown["steps"]["code-1"]["reason"])
        self.assertIn("previous saved step", shown["steps"]["code-2"]["reason"])
        self.assertEqual([], shown["steps"]["code-2"]["files"])

    def test_invalid_commit_identities_never_reach_git(self):
        self.rows[0]["commit"] = "--output=/tmp/x"
        with patch.object(review_diff, "_git") as git:
            shown = review_diff.criterion_diffs(self.root, self.view())
        git.assert_not_called()
        self.assertEqual("unavailable", shown["steps"]["code-1"]["availability"])
        self.assertEqual("unavailable", shown["steps"]["code-2"]["availability"])

    def test_without_plan_or_steps_nothing_is_read(self):
        empty = self.view()
        empty["changes_by_criterion"] = None
        for view in (empty, {**self.view(), "changes_by_criterion": criterion_changes.attribute(self.body, [])}):
            with self.subTest(view=view["changes_by_criterion"]), patch.object(review_diff, "_git") as git:
                shown = review_diff.criterion_diffs(self.root, view)
                self.assertEqual("unavailable", shown["availability"])
                self.assertEqual({}, shown["steps"])
                self.assertTrue(shown["reason"])
                git.assert_not_called()

    def test_oversized_step_file_is_unavailable_not_truncated(self):
        big = self.commit({"a.py": "x" * (review_diff.MAX_FILE_BYTES + 1)})
        self.rows.append({"id": "code-3", "milestone_id": "M1", "changed_files": ["a.py"], "commit": big})
        shown = review_diff.criterion_diffs(self.root, self.view())
        row = shown["steps"]["code-3"]["files"][0]
        self.assertEqual("unavailable", row["availability"])
        self.assertIsNone(row["text"])
        self.assertIn("byte review limit", row["reason"])
        self.assertEqual("partial", shown["availability"])

    def test_render_labels_attribution_and_shows_only_the_criterion_steps(self):
        self.rows[1]["changed_files"].append("a.py")
        shown = review_diff.criterion_diffs(self.root, self.view())
        text = presentation.render_review({"id": "C1", "text": "A changes", "criterion_diffs": shown})
        self.assertIn("Saved Builder steps attributed to this criterion", text)
        self.assertIn("not proof", text)
        self.assertIn("code-1 (milestone M1): Change a", text)
        self.assertIn("+a1", text)
        self.assertNotIn("+b1", text)
        self.assertIn("another milestone also changed", text)
        missing = presentation.render_review({"id": "C1", "text": "A changes"})
        self.assertIn("Saved Builder steps attributed to this criterion", missing)
        self.assertIn("Unavailable in this status view.", missing)

    def test_a_shared_parallel_step_shows_each_criterion_only_its_own_files(self):
        self.rows[1].update(
            milestone_id=None,
            label="Integrate and verify",
            members=[
                {"milestone_id": "M2", "changed_files": ["b.py"]},
                {"milestone_id": "M3", "changed_files": ["new.py"]},
            ],
        )
        self.body["acceptance_criteria"].append({"id": "C3"})
        self.body["milestones"].append({"id": "M3", "acceptance_criteria": ["C3"]})
        shown = review_diff.criterion_diffs(self.root, self.view())
        self.assertEqual(["b.py", "new.py"], [row["path"] for row in shown["steps"]["code-2"]["files"]])
        second = presentation.render_review({"id": "C2", "text": "B changes", "criterion_diffs": shown})
        third = presentation.render_review({"id": "C3", "text": "New file", "criterion_diffs": shown})
        self.assertIn("  code-2 (milestones M2, M3): Integrate and verify", second.splitlines())
        self.assertIn("    b.py", second.splitlines())
        self.assertIn("+b1", second)
        self.assertNotIn("    new.py", second.splitlines())
        self.assertNotIn("/dev/null", second)
        self.assertIn("    Not shown, changed in this step for another milestone: new.py", second.splitlines())
        self.assertIn("    new.py", third.splitlines())
        self.assertNotIn("+b1", third)
        self.assertIn("    Not shown, changed in this step for another milestone: b.py", third.splitlines())


if __name__ == "__main__":
    unittest.main()
