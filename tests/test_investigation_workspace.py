"""The runner provides a complete, isolated repository for bug reproduction."""

import os
import tempfile
import time
import unittest
from pathlib import Path

import autocode_investigation_workspace as investigation_workspace


class ScratchRootTests(unittest.TestCase):
    def test_root_is_inside_workspace_and_does_not_replace_existing_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            evidence = workspace / ".autocode/investigation/previous/result.txt"
            evidence.parent.mkdir(parents=True)
            evidence.write_text("retained")
            root = investigation_workspace.scratch_root(workspace)
            self.assertEqual(workspace.resolve() / ".autocode/investigation", root)
            self.assertEqual("retained", evidence.read_text())

    def test_symlinked_runner_directory_is_rejected_before_external_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace, outside = Path(temporary) / "repo", Path(temporary) / "outside"
            workspace.mkdir()
            outside.mkdir()
            (workspace / ".autocode").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symlink"):
                investigation_workspace.scratch_root(workspace)
            self.assertEqual([], list(outside.iterdir()))


class SourceFilterTests(unittest.TestCase):
    def test_excludes_repository_metadata_and_runner_state_at_every_depth(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.assertEqual(
                {".git", ".autocode"},
                set(
                    investigation_workspace.ignored_entries(
                        root, root, [".git", ".autocode", "accessors", "rule", "vendor", "go.mod"]
                    )
                ),
            )

    def test_internal_file_symlink_is_safe_to_copy_as_contents(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "source.txt").write_text("source")
            (root / "alias.txt").symlink_to("source.txt")
            self.assertEqual(set(), set(investigation_workspace.ignored_entries(root, root, ["alias.txt"])))

    def test_external_state_and_directory_symlinks_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repo"
            root.mkdir()
            outside = Path(temporary) / "outside.txt"
            outside.write_text("private")
            (root / ".autocode").mkdir()
            (root / ".autocode/state.json").write_text("retained")
            (root / "source").mkdir()
            for name, target in [
                ("outside-link", outside),
                ("state-link", root / ".autocode/state.json"),
                ("directory-link", root / "source"),
            ]:
                with self.subTest(name=name):
                    (root / name).symlink_to(target)
                    with self.assertRaisesRegex(ValueError, "symlink"):
                        investigation_workspace.ignored_entries(root, root, [name])


class PrepareTests(unittest.TestCase):
    def test_a_directory_named_venv_without_a_virtualenv_is_still_copied(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "venv").mkdir()
            (root / "venv/source.py").write_text("value = 42\n")
            scratch = investigation_workspace.prepare(root)
            self.assertEqual("value = 42\n", (scratch / "venv/source.py").read_text())

    def test_prepares_all_source_dependencies_and_non_go_resources_without_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            contents = {
                "go.mod": "module example",
                "accessors/datainterface/model.go": "package datainterface",
                "rule/rule.go": "package rule",
                "internal/utils/slice.go": "package utils",
                "vendor/modules.txt": "offline",
                "vendor/native/lib.so": "native",
                "config/settings.yaml": "setting: value",
                ".git/config": "metadata",
                ".autocode/runs/old/state.json": "retained",
            }
            for name, text in contents.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
            scratch = investigation_workspace.prepare(root)
            self.assertEqual(root.resolve() / ".autocode/investigation", scratch.parent)
            for name, text in contents.items():
                if name.startswith((".git/", ".autocode/")):
                    self.assertFalse((scratch / name).exists())
                else:
                    self.assertEqual(text, (scratch / name).read_text(), name)
                self.assertEqual(text, (root / name).read_text(), name)
            (scratch / "internal/utils/slice.go").write_text("scratch edit")
            self.assertEqual("package utils", (root / "internal/utils/slice.go").read_text())

    def test_each_attempt_gets_a_fresh_copy_and_retains_prior_scratch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "code.py").write_text("original")
            first = investigation_workspace.prepare(root)
            (first / "code.py").write_text("first attempt")
            second = investigation_workspace.prepare(root)
            self.assertNotEqual(first, second)
            self.assertEqual("first attempt", (first / "code.py").read_text())
            self.assertEqual("original", (second / "code.py").read_text())

    def test_internal_file_symlink_is_materialized_and_cannot_write_original(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "source.txt").write_text("original")
            (root / "alias.txt").symlink_to("source.txt")
            scratch = investigation_workspace.prepare(root)
            self.assertFalse((scratch / "alias.txt").is_symlink())
            (scratch / "alias.txt").write_text("scratch edit")
            self.assertEqual("original", (root / "source.txt").read_text())


class GitInventoryTests(unittest.TestCase):
    def repository(self, root):
        import subprocess

        subprocess.run(["git", "init", "-q", str(root)], check=True)
        return subprocess

    def test_tracked_input_under_ignore_rule_survives_without_ignored_siblings(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            git = self.repository(root)
            (root / ".gitignore").write_text("public/\n")
            (root / "public").mkdir()
            (root / "public/proof.md").write_text("declared tracked proof")
            (root / "public/secret.txt").write_text("excluded")
            git.run(["git", "-C", str(root), "add", "-f", "public/proof.md"], check=True)
            scratch = investigation_workspace.prepare(root)
            self.assertEqual("declared tracked proof", (scratch / "public/proof.md").read_text())
            self.assertFalse((scratch / "public/secret.txt").exists())

    def test_nonignored_alias_cannot_materialize_an_ignored_secret(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.repository(root)
            (root / ".gitignore").write_text("secret.txt\n")
            (root / "secret.txt").write_text("excluded")
            (root / "alias.txt").symlink_to("secret.txt")
            with self.assertRaisesRegex(ValueError, "symlink"):
                investigation_workspace.prepare(root)

    def test_initialized_nested_git_source_keeps_its_own_inventory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            git = self.repository(root)
            nested = root / "component"
            nested.mkdir()
            self.repository(nested)
            (nested / ".gitignore").write_text("output/\n")
            (nested / "app.py").write_text("value = 1\n")
            git.run(["git", "-C", str(nested), "add", "."], check=True)
            git.run(
                [
                    "git",
                    "-C",
                    str(nested),
                    "-c",
                    "user.name=T",
                    "-c",
                    "user.email=t@example.test",
                    "commit",
                    "-qm",
                    "component",
                ],
                check=True,
            )
            git.run(["git", "-C", str(root), "add", "component"], check=True, capture_output=True)
            (nested / "app.py").write_text("value = 2\n")
            (nested / "public.md").write_text("ordinary untracked input")
            (nested / "output").mkdir()
            (nested / "output/result.txt").write_text("excluded")
            scratch = investigation_workspace.prepare(root)
            self.assertEqual("value = 2\n", (scratch / "component/app.py").read_text())
            self.assertEqual("ordinary untracked input", (scratch / "component/public.md").read_text())
            self.assertFalse((scratch / "component/output").exists())
            self.assertFalse((scratch / "component/.git").exists())


class ReclaimUnusedCopiesTests(unittest.TestCase):
    """A copy no attempt wrote into holds no evidence, so it must not hold a whole tree for the run (#2606)."""

    def make_copy(self, root, name, *, stamp_age, entry_age):
        copy = root / name
        copy.mkdir(parents=True)
        (copy / "code.py").write_text("copied source")
        investigation_workspace.stamp(copy)
        now = time.time()
        for path in (copy, copy / "code.py"):
            os.utime(path, (now - entry_age, now - entry_age))
        os.utime(investigation_workspace.stamp_path(copy), (now - stamp_age, now - stamp_age))
        return copy

    def test_an_untouched_copy_older_than_the_grace_is_removed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / ".autocode/investigation"
            root.mkdir(parents=True)
            stale = self.make_copy(root, "bug-old", stamp_age=900, entry_age=900)
            self.assertEqual([str(stale)], investigation_workspace.reclaim_unused(root))
            self.assertFalse(stale.exists())
            self.assertFalse(investigation_workspace.stamp_path(stale).exists())

    def test_a_copy_an_attempt_wrote_into_is_kept(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / ".autocode/investigation"
            root.mkdir(parents=True)
            written = self.make_copy(root, "bug-worked", stamp_age=900, entry_age=10)
            self.assertEqual([], investigation_workspace.reclaim_unused(root))
            self.assertTrue((written / "code.py").exists())

    def test_a_recent_untouched_copy_is_left_for_its_own_attempt(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / ".autocode/investigation"
            root.mkdir(parents=True)
            fresh = self.make_copy(root, "bug-new", stamp_age=10, entry_age=10)
            self.assertEqual([], investigation_workspace.reclaim_unused(root))
            self.assertTrue(fresh.exists())

    def test_a_copy_recorded_before_stamps_is_left_alone(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / ".autocode/investigation"
            (root / "bug-unstamped").mkdir(parents=True)
            self.assertEqual([], investigation_workspace.reclaim_unused(root))
            self.assertTrue((root / "bug-unstamped").exists())

    def test_a_new_prepare_reclaims_the_earlier_copy_that_no_attempt_used(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "project"
            workspace.mkdir()
            (workspace / "code.py").write_text("original")
            unused = investigation_workspace.prepare(workspace)
            now = time.time()
            past = now - investigation_workspace.RECLAIM_GRACE_SECONDS - 60
            for path in (unused, unused / "code.py"):
                os.utime(path, (past, past))
            os.utime(investigation_workspace.stamp_path(unused), (past, past))
            second = investigation_workspace.prepare(workspace)
            self.assertFalse(unused.exists(), "an untouched copy is not evidence")
            self.assertEqual("original", (second / "code.py").read_text())


if __name__ == "__main__":
    unittest.main()
