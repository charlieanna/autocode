"""Complete execution identity binds only independently inventoried symlink targets."""

import os
import shlex
import unittest
from pathlib import Path

import autocode_import_observation as observation
import autocode_source_snapshot as source_snapshot
import autocode_verify as verify

from tests.test_verify import Project, git, isolated_python


class SourceLinkIdentityTests(unittest.TestCase):
    def setUp(self):
        self.project = Project({"src/product.py": "value = 1\n", "src/other.py": "value = 2\n"})
        self.addCleanup(self.project.close)
        self.root = self.project.root
        self.link = self.root / "alias.py"
        self.link.symlink_to("src/product.py")
        git(self.root, "add", "alias.py")
        git(self.root, "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "link")

    def unbound(self):
        files = source_snapshot.snapshot(self.root)["files"]
        return source_snapshot.unbound_symlinks(self.root, files, lambda: git(self.root, "ls-files", "-z").split("\0"))

    def retarget(self, target):
        self.link.unlink()
        self.link.symlink_to(target)

    def test_in_tree_tracked_target_is_bound_in_execution_identity(self):
        python = isolated_python(self)
        command = shlex.join([python, "-m", "unittest"])
        first = verify.execution_identity(self.root, command=command)
        self.assertTrue(first["cache_binding_complete"], first["unbound_source_symlinks"])
        self.assertEqual([], first["unbound_source_symlinks"])
        (self.root / "src/product.py").write_text("value = 3\n")
        edited = verify.execution_identity(self.root, command=command)
        self.assertTrue(edited["cache_binding_complete"])
        self.assertNotEqual(first["source_revision"], edited["source_revision"])
        self.retarget("src/other.py")
        retargeted = verify.execution_identity(self.root, command=command)
        self.assertTrue(retargeted["cache_binding_complete"])
        self.assertNotEqual(edited["source_revision"], retargeted["source_revision"])

    def test_escape_missing_untracked_and_link_chains_remain_unbound(self):
        (self.root / "untracked.py").write_text("value = 9\n")
        (self.root / "chain.py").symlink_to("alias.py")
        (self.root / "directory").symlink_to("src", target_is_directory=True)
        (self.root / "outside_link").symlink_to(self.root.parent, target_is_directory=True)
        for target in (
            "../outside.py",
            "src/missing.py",
            "untracked.py",
            "chain.py",
            "directory/product.py",
            "directory/../src/product.py",
            "outside_link/../src/product.py",
        ):
            with self.subTest(target=target):
                self.retarget(target)
                self.assertIn("alias.py", self.unbound())

    def test_snapshot_mismatch_cannot_bind_a_retargeted_link_or_modified_target(self):
        original = source_snapshot.snapshot(self.root)["files"]
        tracked = lambda: git(self.root, "ls-files", "-z").split("\0")
        self.retarget("src/other.py")
        self.assertIn("alias.py", source_snapshot.unbound_symlinks(self.root, original, tracked))
        self.retarget("src/product.py")
        (self.root / "src/product.py").write_text("value = 7\n")
        self.assertIn("alias.py", source_snapshot.unbound_symlinks(self.root, original, tracked))

    def test_target_with_an_external_hardlink_stays_unbound(self):
        os.link(self.root / "src/product.py", self.root.parent / "outside-hardlink.py")
        self.assertIn("alias.py", self.unbound())

    def test_target_mutated_during_observation_invalidates_real_execution_identity(self):
        base = Project({"src/product.py": "value = 1\n"})
        self.addCleanup(base.close)
        (base.root / "alias.py").symlink_to("src/product.py")
        source = (
            "import unittest\nclass Feature(unittest.TestCase):\n"
            "    def test_new(self):\n        from alias import new\n        self.assertEqual(new, 42)\n"
        )
        for tree in (base.root, self.root):
            (tree / "test_feature.py").write_text(source)
        (self.root / "src/product.py").write_text("new = 42\n")
        out = Path(self.project.temp.name) / "observation"
        out.mkdir()
        python = isolated_python(self)
        command = shlex.join([python, "-m", "unittest", "-v", "test_feature"])
        framework = verify.Framework("unittest", command, python=python)
        original = verify.run_suite(framework, command, base.root, out, "original", timeout=30)
        snap = lambda tree: source_snapshot.snapshot(tree)["revision"]
        identity = lambda tree: verify.execution_identity(tree, command=shlex.join([python, "-m", "unittest"]))

        def change_after_run(*args, **kwargs):
            receipt = verify.run_suite(*args, **kwargs)
            (self.root / "src/product.py").write_text("new = 43\n")
            return receipt

        observed = observation.observe_candidate(
            original,
            base.root,
            self.root,
            framework,
            original["results"]["failed"],
            command,
            out / "attempt",
            run_suite=change_after_run,
            run_command=verify.run_command,
            snapshot=snap,
            identity=identity,
            expected_source=snap(self.root),
            is_product=lambda path: not path.startswith("test_"),
            timeout=30,
        )
        self.assertEqual({}, observed["imports"])
        self.assertEqual("Candidate execution identity changed after the selected tests", observed["reason"])
        self.assertNotIn("probe", observed)
