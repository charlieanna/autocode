"""Complete execution identity binds only independently inventoried symlink targets."""

import os
import shlex
import unittest

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

    def test_tracked_file_link_chain_is_bound_to_every_link_and_final_bytes(self):
        middle = self.root / "middle.py"
        middle.symlink_to("src/product.py")
        self.retarget("middle.py")
        git(self.root, "add", "alias.py", "middle.py")
        current = source_snapshot.snapshot(self.root)["files"]
        tracked = lambda: git(self.root, "ls-files", "-z").split("\0")
        self.assertEqual([], source_snapshot.unbound_symlinks(self.root, current, tracked))
        middle.unlink()
        middle.symlink_to("src/other.py")
        self.assertIn("alias.py", source_snapshot.unbound_symlinks(self.root, current, tracked))
        middle.unlink()
        middle.symlink_to("src/product.py")
        (self.root / "src/product.py").write_text("value = 9\n")
        self.assertIn("alias.py", source_snapshot.unbound_symlinks(self.root, current, tracked))

    def test_long_acyclic_chain_stays_unbound_before_host_resolution_limits(self):
        names = [f"link_{index}.py" for index in range(9)]
        for index, name in enumerate(names):
            target = names[index + 1] if index + 1 < len(names) else "src/product.py"
            (self.root / name).symlink_to(target)
        git(self.root, "add", *names)
        unbound = self.unbound()
        self.assertIn(names[0], unbound)
        self.assertNotIn(names[1], unbound)
        self.assertEqual("value = 1\n", (self.root / names[1]).read_text())

    def test_tracked_chain_rejects_cycles_escapes_and_intermediate_directory_links(self):
        middle = self.root / "middle.py"
        middle.symlink_to("src/product.py")
        self.retarget("middle.py")
        git(self.root, "add", "alias.py", "middle.py")
        (self.root / "outside-dir").symlink_to(self.root.parent, target_is_directory=True)
        for target in (
            "alias.py",
            "../outside.py",
            str(self.root / "src/product.py"),
            "outside-dir/../src/product.py",
            "src/product.py/.",
            "alias.py/.",
            "src\\product.py",
            "src/missing.py",
        ):
            with self.subTest(target=target):
                middle.unlink()
                middle.symlink_to(target)
                self.assertIn("alias.py", self.unbound())

    def test_copy_policy_accepts_only_inventoried_untracked_terminal_files(self):
        target = self.root / "new_product.py"
        target.write_text("value = 9\n")
        self.retarget("new_product.py")
        files = source_snapshot.snapshot(self.root)["files"]
        tracked = lambda: git(self.root, "ls-files", "-z").split("\0")
        self.assertIn("alias.py", source_snapshot.unbound_symlinks(self.root, files, tracked))
        self.assertEqual([], source_snapshot.unbound_symlinks(self.root, files, tracked, allow_untracked_terminal=True))
        target.write_text("value = 10\n")
        self.assertIn(
            "alias.py", source_snapshot.unbound_symlinks(self.root, files, tracked, allow_untracked_terminal=True)
        )

    def test_copy_policy_still_refuses_untracked_hops_and_uninventoried_targets(self):
        middle = self.root / "untracked_middle.py"
        middle.symlink_to("src/product.py")
        self.retarget("untracked_middle.py")
        tracked = lambda: git(self.root, "ls-files", "-z").split("\0")
        files = source_snapshot.snapshot(self.root)["files"]
        self.assertIn(
            "alias.py", source_snapshot.unbound_symlinks(self.root, files, tracked, allow_untracked_terminal=True)
        )
        target = self.root / "untracked_target.py"
        target.write_text("value = 9\n")
        self.retarget("untracked_target.py")
        files = source_snapshot.snapshot(self.root)["files"]
        del files["untracked_target.py"]
        self.assertIn(
            "alias.py", source_snapshot.unbound_symlinks(self.root, files, tracked, allow_untracked_terminal=True)
        )
        os.link(target, self.root.parent / "external-hardlink.py")
        files = source_snapshot.snapshot(self.root)["files"]
        self.assertIn(
            "alias.py", source_snapshot.unbound_symlinks(self.root, files, tracked, allow_untracked_terminal=True)
        )

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
