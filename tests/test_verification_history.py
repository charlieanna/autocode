"""Real Git delivery checks run against isolated, source-bound verification copies."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_source_snapshot as source
import autocode_verification_copy as copies


class VerificationHistoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / "project"
        self.root.mkdir()
        self.git("init", "-q")
        (self.root / ".gitignore").write_text(".autocode/\nignored.txt\n")
        (self.root / "export.py").write_text("original\n")
        self.commit("Seed")
        self.base = self.git("rev-parse", "HEAD").strip()
        (self.root / "export.py").write_text("implemented\n")
        (self.root / "test_export.py").write_text("regression\n")
        self.commit("Deliver exporter and its tests")
        self.head = self.git("rev-parse", "HEAD").strip()
        self.scratch = self.root / ".autocode/tool-containment-history/scratch"
        self.scratch.mkdir(parents=True)

    def git(self, *args, root=None):
        return subprocess.check_output(["git", "-C", str(root or self.root), *args], text=True)

    def commit(self, message):
        self.git("add", ".")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", message)

    def prepare(self, **kwargs):
        result = copies.create(self.root, self.scratch, **kwargs)
        tree, provenance = copies.execution(result["manifest"], result["sha256"], self.root)
        return tree, result, provenance

    def test_original_atomic_commit_and_its_parent_are_visible_in_the_copy(self):
        before = source.snapshot(self.root)
        tree, _, provenance = self.prepare()
        self.assertEqual(self.head, self.git("rev-parse", "HEAD", root=tree).strip())
        self.assertEqual(self.base, self.git("rev-parse", "HEAD^", root=tree).strip())
        paths = self.git("diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD", root=tree).splitlines()
        self.assertEqual(["export.py", "test_export.py"], paths)
        self.assertEqual(before, source.snapshot(self.root))
        self.assertEqual(before["revision"], provenance["source_revision"])

    def test_current_uncommitted_source_still_overlays_the_retained_history(self):
        (self.root / "export.py").write_text("later work\n")
        (self.root / "new.py").write_text("new file\n")
        (self.root / "test_export.py").unlink()
        (self.root / "ignored.txt").write_text("local-only\n")
        tree, _, _ = self.prepare()
        self.assertEqual("later work\n", (tree / "export.py").read_text())
        self.assertEqual("new file\n", (tree / "new.py").read_text())
        self.assertFalse((tree / "test_export.py").exists())
        self.assertFalse((tree / "ignored.txt").exists())
        self.assertEqual("implemented\n", self.git("show", "HEAD:export.py", root=tree))

    def test_history_is_standalone_and_original_config_hooks_and_extra_refs_are_not_copied(self):
        self.git("config", "secret.example", "must-not-copy")
        self.git("config", "remote.origin.url", "/unavailable/remote")
        (self.root / ".git/hooks/example").write_text("must-not-copy\n")
        self.git("branch", "unrelated", self.base)
        # Replaced history must not be mistaken for the actual delivered commit.
        self.git("replace", self.head, self.base)
        tree, _, _ = self.prepare()
        self.assertEqual(self.base, self.git("rev-parse", "HEAD^", root=tree).strip())
        self.assertFalse((tree / ".git/objects/info/alternates").exists())
        self.assertFalse((tree / ".git/hooks/example").exists())
        self.assertEqual("", self.git("for-each-ref", "--format=%(refname)", root=tree))
        config = self.git("config", "--local", "--list", root=tree)
        self.assertNotIn("secret.example", config)
        self.assertNotIn("remote.origin", config)
        original_git = self.root / ".git"
        original_git.rename(self.root / "saved-git")
        self.addCleanup(lambda: (self.root / "saved-git").rename(original_git))
        paths = self.git("diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD", root=tree).splitlines()
        self.assertEqual(["export.py", "test_export.py"], paths)
        self.git("fsck", "--no-reflogs", root=tree)

    def test_linked_worktree_history_is_retained_without_sharing_its_git_directory(self):
        linked = self.root.parent / "linked"
        self.git("worktree", "add", "--detach", str(linked), self.head)
        self.addCleanup(lambda: self.git("worktree", "remove", "--force", str(linked)))
        scratch = linked / ".autocode/tool-containment-history/scratch"
        scratch.mkdir(parents=True)
        result = copies.create(linked, scratch)
        tree, _ = copies.execution(result["manifest"], result["sha256"], linked)
        self.assertTrue((tree / ".git").is_dir())
        self.assertEqual(self.head, self.git("rev-parse", "HEAD", root=tree).strip())
        self.assertEqual(self.base, self.git("rev-parse", "HEAD^", root=tree).strip())

    def test_shallow_clone_retains_its_declared_history_boundary(self):
        shallow = self.root.parent / "shallow"
        subprocess.run(["git", "clone", "-q", "--depth=1", self.root.as_uri(), str(shallow)], check=True)
        scratch = shallow / ".autocode/tool-containment-history/scratch"
        scratch.mkdir(parents=True)
        result = copies.create(shallow, scratch)
        tree, _ = copies.execution(result["manifest"], result["sha256"], shallow)
        self.assertEqual(self.head, self.git("rev-parse", "HEAD", root=tree).strip())
        self.assertEqual("true", self.git("rev-parse", "--is-shallow-repository", root=tree).strip())
        self.assertEqual(self.head + "\n", self.git("rev-list", "HEAD", root=tree))
        self.git("fsck", "--no-reflogs", root=tree)

    def test_fixed_native_setup_sequence_also_receives_real_history(self):
        setup_calls = []

        def execute(command, **kwargs):
            setup_calls.append(command)
            result = subprocess.run(command, capture_output=True, **kwargs)
            return {"exit_code": result.returncode}

        tree, _, _ = self.prepare(setup_executor=execute)
        self.assertEqual([["/usr/bin/git", *args] for args in copies.GIT_SETUP_ARGUMENTS], setup_calls)
        self.assertEqual(self.head, self.git("rev-parse", "HEAD", root=tree).strip())
        self.assertEqual(self.base, self.git("rev-parse", "HEAD^", root=tree).strip())

    def test_sha256_history_uses_the_source_object_format_for_both_setup_paths(self):
        root = self.root.parent / "sha256"
        root.mkdir()
        self.git("init", "-q", "--object-format=sha256", root=root)
        (root / "file.txt").write_text("sha256 source\n")
        self.git("add", ".", root=root)
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "Seed", root=root)
        head = self.git("rev-parse", "HEAD", root=root).strip()

        def execute(command, **kwargs):
            result = subprocess.run(command, capture_output=True, **kwargs)
            return {"exit_code": result.returncode}

        for label, executor in [("ordinary", None), ("native", execute)]:
            with self.subTest(label=label):
                scratch = root / (".autocode/tool-containment-" + label + "/scratch")
                scratch.mkdir(parents=True)
                result = copies.create(root, scratch, setup_executor=executor)
                tree, _ = copies.execution(result["manifest"], result["sha256"], root)
                self.assertEqual("sha256", self.git("rev-parse", "--show-object-format", root=tree).strip())
                self.assertEqual(head, self.git("rev-parse", "HEAD", root=tree).strip())

    def test_source_commit_changes_during_export_prevent_publication(self):
        real = subprocess.run

        def change(command, **kwargs):
            result = real(command, **kwargs)
            if "pack-objects" in command:
                self.git(
                    "-c",
                    "user.name=Test",
                    "-c",
                    "user.email=test@example.invalid",
                    "commit",
                    "--allow-empty",
                    "-qm",
                    "Concurrent commit",
                )
            return result

        with mock.patch.object(copies.subprocess, "run", side_effect=change):
            with self.assertRaisesRegex(ValueError, "Source changed while preparing"):
                self.prepare()
        self.assertFalse((self.scratch.parent / "verification-copy.json").exists())

    def test_retained_git_objects_are_protected_by_the_copy_manifest(self):
        tree, result, _ = self.prepare()
        data = json.loads(Path(result["manifest"]).read_text())
        packs = list((tree / ".git/objects/pack").glob("*.pack"))
        self.assertTrue(packs, "a standalone history pack must be retained")
        self.assertIn(str(packs[0].relative_to(tree)), data["files"])
        packs[0].chmod(0o600)
        packs[0].write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "Verification input changed"):
            copies.execution(result["manifest"], result["sha256"], self.root)

    def test_history_export_failure_never_publishes_a_copy(self):
        real = subprocess.run

        def fail(command, **kwargs):
            if "pack-objects" in command:
                raise subprocess.CalledProcessError(128, command, stderr=b"cannot export Git history")
            return real(command, **kwargs)

        with mock.patch.object(copies.subprocess, "run", side_effect=fail):
            with self.assertRaises(subprocess.CalledProcessError):
                self.prepare()
        self.assertFalse((self.scratch.parent / "verification-copy.json").exists())

    def test_history_export_uses_the_existing_preparation_deadline_and_clean_environment(self):
        real = subprocess.run
        exports = []

        def inspect(command, **kwargs):
            if "pack-objects" in command:
                exports.append(kwargs)
            return real(command, **kwargs)

        with mock.patch.object(copies.subprocess, "run", side_effect=inspect):
            self.prepare(_deadline=copies.time.monotonic() + 10)
        self.assertEqual(1, len(exports))
        self.assertGreater(exports[0]["timeout"], 0)
        self.assertLessEqual(exports[0]["timeout"], 10)
        self.assertEqual("1", exports[0]["env"]["GIT_NO_REPLACE_OBJECTS"])
        self.assertEqual("1", exports[0]["env"]["GIT_NO_LAZY_FETCH"])
        self.assertNotIn("GIT_DIR", exports[0]["env"])
        self.assertNotIn("GIT_OBJECT_DIRECTORY", exports[0]["env"])


if __name__ == "__main__":
    unittest.main()
