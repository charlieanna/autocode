"""Exact, located patch edits: no whole-file line borrowing or whitespace normalization."""
import difflib
import unittest

import autocode_base_patch as base_patch
from autocode_patch_containment import contains_edits
from tests.test_verify import Project, git


class EditContainmentTests(unittest.TestCase):
    def test_deletion_is_not_contained_in_an_unchanged_original(self):
        self.assertFalse(contains_edits("guard()\nact()\n", "act()\n", "# docs\nguard()\nact()\n"))

    def test_an_addition_cannot_borrow_existing_text_at_another_location(self):
        original = "def a():\n    return 0\n\ndef b():\n    return -1\n"
        self.assertFalse(contains_edits(original, original.replace("return 0", "return -1"), "# doc\n" + original))
        self.assertFalse(contains_edits(original, original.replace("return 0", "return -1\n    return 0"), original))

    def test_candidate_changes_in_a_different_function_do_not_satisfy_the_patch(self):
        original = "def a():\n    call()\n\ndef b():\n    call()\n"
        patch = original.replace("call()", "hook()", 1)
        candidate = original.rsplit("call()", 1)[0] + "hook()\n"
        self.assertFalse(contains_edits(original, patch, candidate))

    def test_valid_seam_survives_removing_its_surrounding_exception_handler(self):
        original = "try:\n    os.rename(src, dst)\nexcept OSError:\n    pass\n"
        self.assertTrue(contains_edits(original, original.replace("os.rename", "replace_file"),
                                      "replace_file(src, dst)\n"))

    def test_whitespace_inside_literals_is_not_normalized(self):
        original = 'message = "before"\n'
        self.assertFalse(contains_edits(original, 'message = " after "\n', 'message = "after"\n'))

    def test_indentation_and_line_endings_are_not_normalized(self):
        original = "if ok:\n    run()\nfinish()\n"
        self.assertFalse(contains_edits(original, "if ok:\n    run()\n    finish()\n", original))
        self.assertFalse(contains_edits("line\n", "line\r\n", "line\n"))

    def test_repeated_patch_additions_need_separate_candidate_occurrences(self):
        original = "a b c d\n"
        self.assertFalse(contains_edits(original, "a X X d\n", "a X d\n"))
        self.assertTrue(contains_edits(original, "a X X d\n", "a X/X d\n"))

    def test_patch_additions_cannot_be_reordered_in_a_larger_candidate_edit(self):
        original = "a b c d\n"
        self.assertFalse(contains_edits(original, "a X Y d\n", "a Y/X d\n"))
        self.assertTrue(contains_edits(original, "a X Y d\n", "a X/Y d\n"))

    def test_larger_candidate_deletion_contains_the_patch_deletion(self):
        self.assertTrue(contains_edits("a b c d\n", "a c d\n", "a d\n"))

    def test_large_repeated_unchanged_regions_remain_anchored(self):
        original = "start\n" + "unchanged()\n" * 10000 + "end\n"
        self.assertTrue(contains_edits(original, original + "hook()\n", "# docs\n" + original + "hook()\n"))

    def test_expensive_ambiguous_replacement_is_refused(self):
        with self.assertRaisesRegex(ValueError, "too repetitive"):
            contains_edits("a " * 2000, "b " * 2000, "c " * 2000)


class PatchFileContainmentTests(unittest.TestCase):
    def setUp(self):
        self.project = Project({"app.py": "value = 1\n", "test_app.py": "# protected\n"})
        self.addCleanup(self.project.close)
        self.root = self.project.root

    def pin(self, patch):
        path = self.root / ".autocode" / "operator.patch"
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(patch if isinstance(patch, bytes) else patch.encode())
        return base_patch.pin(path, self.root, self.project.base)

    def diff(self, name, before, after):
        return "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                            "a/" + name, "b/" + name))

    def test_file_deletion_requires_candidate_deletion(self):
        saved = self.pin("diff --git a/app.py b/app.py\ndeleted file mode 100644\n"
                         "--- a/app.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-value = 1\n")
        self.assertIn("deletes a file retained", base_patch.check(saved, self.root, self.project.base)[1])
        (self.root / "app.py").unlink()
        self.assertEqual("", base_patch.check(saved, self.root, self.project.base)[1])

    def test_new_file_requires_the_actual_content(self):
        saved = self.pin("diff --git a/hook.py b/hook.py\nnew file mode 100644\n"
                         "--- /dev/null\n+++ b/hook.py\n@@ -0,0 +1 @@\n+HOOK = None\n")
        self.project.write({"hook.py": "HOOK = 3\n"})
        self.assertIn("file differs", base_patch.check(saved, self.root, self.project.base)[1])
        self.project.write({"hook.py": "HOOK = None\n"})
        self.assertEqual("", base_patch.check(saved, self.root, self.project.base)[1])

    def test_quoted_git_path_cannot_bypass_test_protection(self):
        patch = ('diff --git "a/\\164est_app.py" "b/\\164est_app.py"\n'
                 '--- "a/\\164est_app.py"\n+++ "b/\\164est_app.py"\n'
                 '@@ -1 +1 @@\n-# protected\n+# weakened\n')
        with self.assertRaisesRegex(ValueError, "may not change test files.*test_app.py"):
            self.pin(patch)

    def test_candidate_symlink_is_not_followed_as_source(self):
        saved = self.pin(self.diff("app.py", "value = 1\n", "value = 2\n"))
        self.project.write({"elsewhere.py": "value = 2\n"})
        (self.root / "app.py").unlink()
        (self.root / "app.py").symlink_to("elsewhere.py")
        self.assertIn("not a regular", base_patch.check(saved, self.root, self.project.base)[1])

    def test_executable_bit_change_must_be_present(self):
        saved = self.pin("diff --git a/app.py b/app.py\nold mode 100644\nnew mode 100755\n")
        self.assertIn("mode change is absent", base_patch.check(saved, self.root, self.project.base)[1])
        (self.root / "app.py").chmod(0o755)
        self.assertEqual("", base_patch.check(saved, self.root, self.project.base)[1])

    def test_patch_inspection_preserves_the_real_index_and_worktree(self):
        self.project.write({"app.py": "value = 9\n"})
        git(self.root, "add", "app.py")
        index_before = git(self.root, "write-tree")
        saved = self.pin(self.diff("app.py", "value = 1\n", "value = 2\n"))
        base_patch.check(saved, self.root, self.project.base)
        self.assertEqual(index_before, git(self.root, "write-tree"))
        self.assertEqual("value = 9\n", (self.root / "app.py").read_text())
