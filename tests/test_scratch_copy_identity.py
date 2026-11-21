"""A scratch execution must copy the pinned source before intentional overlays."""

import os
import shlex
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_scratch_preparation as scratch_preparation
import autocode_source_snapshot as source_snapshot
import autocode_util as util
import autocode_verification_schedule as schedule
import autocode_verify as verify

from tests.test_verify import Project, git


class ScratchCopyIdentityTests(unittest.TestCase):
    def setUp(self):
        self.project = Project({"app.py": "value = 0\n", ".gitignore": "docs/\n"})
        self.addCleanup(self.project.close)
        self.workspace = self.project.root
        self.app = self.workspace / "app.py"
        self.app.write_text("value = 1\n")
        self.marker = Path(self.project.temp.name) / "command-ran"
        self.command = shlex.join(
            [sys.executable, "-c", f"from pathlib import Path; Path({str(self.marker)!r}).touch()"]
        )

    def run_copy(self, **kwargs):
        return verify.scratch_run(self.workspace, self.project.evidence, command=self.command, timeout=30, **kwargs)

    def test_a_deleted_tracked_file_is_absent_from_a_pinned_copy(self):
        self.app.unlink()
        pinned = source_snapshot.snapshot(self.workspace)
        self.assertEqual("deleted", pinned["files"]["app.py"])
        receipt = self.run_copy(expected_source_revision=pinned["revision"])
        self.assertEqual(0, receipt["exit_code"], receipt)
        self.assertTrue(self.marker.exists())

    def test_resurrecting_a_deleted_tracked_file_during_copy_is_refused(self):
        self.app.unlink()
        pinned = source_snapshot.snapshot(self.workspace)
        make_tree = verify.make_tree

        def resurrect(*args, **kwargs):
            tree = make_tree(*args, **kwargs)
            (tree / "app.py").write_text("untrusted\n")
            return tree

        with patch.object(verify, "make_tree", side_effect=resurrect):
            with self.assertRaises(util.Paused):
                self.run_copy(expected_source_revision=pinned["revision"])
        self.assertFalse(self.marker.exists())

    def test_an_owned_scratch_tree_inside_the_workspace_is_not_source_drift(self):
        run_dir = self.workspace / "evidence/probe"
        receipt = verify.scratch_run(self.workspace, run_dir, command=self.command, timeout=30)
        self.assertEqual(0, receipt["exit_code"], receipt)
        self.assertTrue(self.marker.exists())

    def test_other_new_source_beside_an_owned_scratch_tree_is_still_refused(self):
        run_dir = self.workspace / "evidence/probe"
        make_tree = verify.make_tree

        def inject(*args, **kwargs):
            tree = make_tree(*args, **kwargs)
            (run_dir / "unexpected.py").write_text("untrusted\n")
            return tree

        with patch.object(verify, "make_tree", side_effect=inject):
            with self.assertRaises(util.Paused):
                verify.scratch_run(self.workspace, run_dir, command=self.command, timeout=30)
        self.assertFalse(self.marker.exists())

    def test_owned_tree_exception_never_hides_preexisting_or_selected_source(self):
        run_dir = self.workspace / "evidence/probe"
        tree = run_dir / "scratch/tree"
        key = "evidence/probe/scratch/tree/"
        before = {"head": "base", "files": {key: "submodule:before"}}
        after = {"head": "base", "files": {key: "submodule:after"}}
        self.assertFalse(scratch_preparation._same_source_after_copy(self.workspace, run_dir, tree, before, after, ()))
        before["files"] = {}
        self.assertFalse(
            scratch_preparation._same_source_after_copy(self.workspace, run_dir, tree, before, after, ("evidence",))
        )

    def test_commit_during_copy_is_refused_even_when_source_files_are_unchanged(self):
        original = source_snapshot.snapshot(self.workspace)
        make_tree = verify.make_tree

        def advance_head(*args, **kwargs):
            tree = make_tree(*args, **kwargs)
            git(
                self.workspace,
                "-c",
                "user.name=t",
                "-c",
                "user.email=t@example.test",
                "commit",
                "--allow-empty",
                "-qm",
                "new head",
            )
            current = source_snapshot.snapshot(self.workspace)
            self.assertNotEqual(original["head"], current["head"])
            self.assertEqual(original["files"], current["files"])
            return tree

        with patch.object(verify, "make_tree", side_effect=advance_head):
            with self.assertRaises(util.Paused) as held:
                self.run_copy()
        self.assertEqual("PAUSED_SOURCE_UNAVAILABLE", held.exception.status)
        self.assertFalse(self.marker.exists(), "The check must not launch against a stale source revision")

    def test_owned_worker_preserves_unsafe_copy_path_as_source_refusal(self):
        shared = self.workspace / "shared"
        shared.mkdir()
        (shared / "test_original.py").write_text("original = True\n")
        git(self.workspace, "add", "shared")
        git(self.workspace, "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "source")
        external = Path(self.project.temp.name) / "external"
        external.mkdir()
        (external / "test_original.py").write_text("external = True\n")
        shared.rename(self.workspace / "old-shared")
        shared.symlink_to(external, target_is_directory=True)
        identity = {"fixture": "unsafe copied parent"}
        with self.assertRaises(util.Paused) as held:
            schedule.run(
                self.project.evidence / "obligations",
                identity,
                lambda out: verify.scratch_run(self.workspace, out, command=self.command, timeout=30),
                reuse_allowed=False,
                reason="fresh",
                current_identity=lambda: identity,
                owned_preparation=True,
            )
        self.assertEqual("PAUSED_SOURCE_UNAVAILABLE", held.exception.status)
        self.assertIn("symlinked parent", str(held.exception))
        self.assertFalse(self.marker.exists())
        self.assertEqual("external = True\n", (external / "test_original.py").read_text())

    def test_transient_copy_change_is_rejected_even_if_workspace_is_restored(self):
        pinned = source_snapshot.snapshot(self.workspace)["revision"]
        make_tree = verify.make_tree

        def transient(*args, **kwargs):
            self.app.write_text("value = 2\n")
            try:
                return make_tree(*args, **kwargs)
            finally:
                self.app.write_text("value = 1\n")

        with patch.object(verify, "make_tree", side_effect=transient):
            with self.assertRaises(util.Paused) as refused:
                self.run_copy()
        self.assertEqual("PAUSED_SOURCE_UNAVAILABLE", refused.exception.status)
        self.assertEqual(pinned, source_snapshot.snapshot(self.workspace)["revision"])
        self.assertFalse(self.marker.exists())
        self.assertFalse((self.project.evidence / "scratch/tree").exists())

    def test_transient_new_source_path_is_rejected_before_copy(self):
        extra = self.workspace / "injected.py"
        changed_files = verify.changed_files

        def transient(*args, **kwargs):
            extra.write_text("injected = True\n")
            try:
                return changed_files(*args, **kwargs)
            finally:
                extra.unlink()

        with patch.object(verify, "changed_files", side_effect=transient):
            with self.assertRaises(util.Paused) as refused:
                self.run_copy()
        self.assertEqual("PAUSED_SOURCE_UNAVAILABLE", refused.exception.status)
        self.assertFalse(self.marker.exists())

    def test_copy_at_a_different_git_head_is_rejected_even_when_files_match(self):
        original = git(self.workspace, "rev-parse", "HEAD")
        git(
            self.workspace,
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.test",
            "commit",
            "--allow-empty",
            "-qm",
            "other",
        )
        other = git(self.workspace, "rev-parse", "HEAD")
        git(self.workspace, "reset", "--hard", original)
        self.app.write_text("value = 1\n")
        real_git = verify._git

        def mismatched_head(repo, *args, **kwargs):
            if Path(repo) == self.workspace and args == ("rev-parse", "HEAD"):
                return other
            return real_git(repo, *args, **kwargs)

        with patch.object(verify, "_git", side_effect=mismatched_head):
            with self.assertRaises(util.Paused):
                self.run_copy()
        self.assertFalse(self.marker.exists())

    def test_dependency_link_to_a_foreign_environment_is_rejected(self):
        with (self.workspace / ".gitignore").open("a") as stream:
            stream.write(".venv/\n")
        (self.workspace / ".venv").mkdir()
        foreign = Path(self.project.temp.name) / "foreign-venv"
        foreign.mkdir()
        make_tree = verify.make_tree

        def hijacked(*args, **kwargs):
            tree = make_tree(*args, **kwargs)
            (tree / ".venv").unlink()
            (tree / ".venv").symlink_to(foreign, target_is_directory=True)
            return tree

        with patch.object(verify, "make_tree", side_effect=hijacked):
            with self.assertRaises(util.Paused):
                self.run_copy()
        self.assertFalse(self.marker.exists())

    def test_dependency_source_retarget_during_copy_is_rejected(self):
        with (self.workspace / ".gitignore").open("a") as stream:
            stream.write(".venv/\n")
        original = self.workspace / ".venv"
        original.mkdir()
        saved = self.workspace / ".venv-saved"
        foreign = Path(self.project.temp.name) / "foreign-venv"
        foreign.mkdir()
        make_tree = verify.make_tree

        def transient(*args, **kwargs):
            original.rename(saved)
            original.symlink_to(foreign, target_is_directory=True)
            try:
                return make_tree(*args, **kwargs)
            finally:
                original.unlink()
                saved.rename(original)

        with patch.object(verify, "make_tree", side_effect=transient):
            with self.assertRaises(util.Paused):
                self.run_copy()
        self.assertFalse(self.marker.exists())

    def test_pinned_copy_keeps_the_authorized_virtualenv_link(self):
        with (self.workspace / ".gitignore").open("a") as stream:
            stream.write(".venv/\n")
        environment = self.workspace / ".venv"
        environment.mkdir()
        (environment / "marker").write_text("authorized\n")
        pinned = source_snapshot.snapshot(self.workspace)["revision"]
        command = shlex.join(
            [
                sys.executable,
                "-c",
                "from pathlib import Path; assert Path('.venv/marker').read_text() == 'authorized\\n'",
            ]
        )
        receipt = verify.scratch_run(
            self.workspace, self.project.evidence, command=command, timeout=30, expected_source_revision=pinned
        )
        self.assertEqual(0, receipt["exit_code"], receipt)

    def test_pinned_copy_rejects_dependency_retarget_during_the_command(self):
        with (self.workspace / ".gitignore").open("a") as stream:
            stream.write(".venv/\n")
        (self.workspace / ".venv").mkdir()
        foreign = Path(self.project.temp.name) / "foreign-venv"
        foreign.mkdir()
        pinned = source_snapshot.snapshot(self.workspace)["revision"]
        command = shlex.join(
            [
                sys.executable,
                "-c",
                "from pathlib import Path; p=Path('.venv'); p.unlink(); "
                f"p.symlink_to({str(foreign)!r}, target_is_directory=True)",
            ]
        )
        with self.assertRaises(util.Paused):
            verify.scratch_run(
                self.workspace, self.project.evidence, command=command, timeout=30, expected_source_revision=pinned
            )
        self.assertFalse((self.project.evidence / "scratch/tree").exists())

    def test_pinned_copy_rejects_a_new_dependency_directory_during_the_command(self):
        pinned = source_snapshot.snapshot(self.workspace)["revision"]
        command = shlex.join([sys.executable, "-c", "from pathlib import Path; Path('.venv').mkdir()"])
        with self.assertRaises(util.Paused):
            verify.scratch_run(
                self.workspace, self.project.evidence, command=command, timeout=30, expected_source_revision=pinned
            )

    def test_pinned_copy_binds_root_and_package_dependency_links(self):
        package = self.workspace / "packages/client"
        package.mkdir(parents=True)
        (self.workspace / "package.json").write_text("{}\n")
        (package / "package.json").write_text("{}\n")
        with (self.workspace / ".gitignore").open("a") as stream:
            stream.write("node_modules/\n")
        git(self.workspace, "add", ".gitignore", "package.json", "packages/client/package.json")
        git(self.workspace, "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "packages")
        for root, text in ((self.workspace, "root"), (package, "package")):
            module = root / "node_modules/library"
            module.mkdir(parents=True)
            (module / "value.txt").write_text(text)
        pinned = source_snapshot.snapshot(self.workspace)["revision"]
        read = shlex.join(
            [
                sys.executable,
                "-c",
                "from pathlib import Path; "
                "assert Path('node_modules/library/value.txt').read_text() == 'root'; "
                "assert Path('packages/client/node_modules/library/value.txt').read_text() == 'package'",
            ]
        )
        receipt = verify.scratch_run(
            self.workspace, self.project.evidence, command=read, timeout=30, expected_source_revision=pinned
        )
        self.assertEqual(0, receipt["exit_code"], receipt)
        foreign = Path(self.project.temp.name) / "foreign-modules"
        foreign.mkdir()
        retarget = shlex.join(
            [
                sys.executable,
                "-c",
                "from pathlib import Path; p=Path('packages/client/node_modules'); p.unlink(); "
                f"p.symlink_to({str(foreign)!r}, target_is_directory=True)",
            ]
        )
        with self.assertRaises(util.Paused):
            verify.scratch_run(
                self.workspace, self.project.evidence, command=retarget, timeout=30, expected_source_revision=pinned
            )

    def test_owned_worker_rejects_dependency_retarget_after_the_command(self):
        with (self.workspace / ".gitignore").open("a") as stream:
            stream.write(".venv/\n")
        (self.workspace / ".venv").mkdir()
        foreign = Path(self.project.temp.name) / "foreign-venv"
        foreign.mkdir()
        pinned = source_snapshot.snapshot(self.workspace)["revision"]
        command = shlex.join(
            [
                sys.executable,
                "-c",
                "from pathlib import Path; p=Path('.venv'); p.unlink(); "
                f"p.symlink_to({str(foreign)!r}, target_is_directory=True)",
            ]
        )
        identity = {"fixture": "dependency link tampering"}
        with self.assertRaises(util.Paused):
            schedule.run(
                self.project.evidence / "obligations",
                identity,
                lambda out: verify.scratch_run(
                    self.workspace,
                    out,
                    command=command,
                    timeout=30,
                    expected_source_revision=pinned,
                ),
                reuse_allowed=False,
                reason="fresh",
                current_identity=lambda: identity,
                owned_preparation=True,
            )

    def test_pinned_ignored_scope_is_checked_before_intentional_overlay(self):
        document = self.workspace / "docs/result.md"
        document.parent.mkdir()
        document.write_text("approved source\n")
        paths = ["docs/result.md"]
        pinned = source_snapshot.snapshot(self.workspace, paths=paths)["revision"]
        overlay = Path(self.project.temp.name) / "overlay.py"
        overlay.write_text("value = 2\n")
        command = shlex.join(
            [sys.executable, "-c", "from pathlib import Path; assert Path('app.py').read_text() == 'value = 2\\n'"]
        )
        receipt = verify.scratch_run(
            self.workspace,
            self.project.evidence,
            command=command,
            timeout=30,
            source_paths=paths,
            expected_source_revision=pinned,
            files={"app.py": str(overlay)},
        )
        self.assertEqual(0, receipt["exit_code"], receipt)
        document.write_text("changed after pin\n")
        with self.assertRaises(util.Paused):
            self.run_copy(source_paths=paths, expected_source_revision=pinned)
        with self.assertRaises(util.Paused):
            self.run_copy(source_paths=[], expected_source_revision=pinned)
        self.assertFalse(self.marker.exists())

    def test_owned_worker_checks_the_same_pinned_revision(self):
        pinned = source_snapshot.snapshot(self.workspace)["revision"]
        self.app.write_text("changed after pin\n")
        identity = {"fixture": "pinned scratch source"}
        with self.assertRaises(util.Paused):
            schedule.run(
                self.project.evidence / "obligations",
                identity,
                lambda out: verify.scratch_run(
                    self.workspace,
                    out,
                    command=self.command,
                    timeout=30,
                    expected_source_revision=pinned,
                ),
                reuse_allowed=False,
                reason="fresh",
                current_identity=lambda: identity,
                owned_preparation=True,
            )
        self.assertFalse(self.marker.exists())

    def test_owned_worker_copies_the_pinned_ignored_scope(self):
        document = self.workspace / "docs/result.md"
        document.parent.mkdir()
        document.write_text("sealed source\n")
        paths = ["docs/result.md"]
        pinned = source_snapshot.snapshot(self.workspace, paths=paths)["revision"]
        command = shlex.join(
            [
                sys.executable,
                "-c",
                "from pathlib import Path; assert Path('docs/result.md').read_text() == 'sealed source\\n'",
            ]
        )
        identity = {"fixture": "sealed ignored source"}
        result = schedule.run(
            self.project.evidence / "obligations",
            identity,
            lambda out: verify.scratch_run(
                self.workspace, out, command=command, timeout=30, source_paths=paths, expected_source_revision=pinned
            ),
            reuse_allowed=False,
            reason="fresh",
            current_identity=lambda: identity,
            owned_preparation=True,
        )
        self.assertEqual(0, result["exit_code"], result)

    def test_pinned_copy_refuses_an_unbound_external_symlink(self):
        outside = Path(self.project.temp.name) / "external.py"
        outside.write_text("value = 9\n")
        (self.workspace / "external_link.py").symlink_to(outside)
        pinned = source_snapshot.snapshot(self.workspace)["revision"]
        with self.assertRaisesRegex(util.Paused, "unbound symlink target"):
            self.run_copy(expected_source_revision=pinned)
        self.assertFalse(self.marker.exists())

    def test_owned_worker_rejects_a_transient_copy_even_after_source_is_restored(self):
        pinned = source_snapshot.snapshot(self.workspace)["revision"]
        hook = Path(self.project.temp.name) / "worker-hook"
        hook.mkdir()
        trigger = Path(self.project.temp.name) / "worker-triggered"
        hook.joinpath("sitecustomize.py").write_text(
            "import pathlib, shutil\n"
            "original = shutil.copy2\n"
            "def transient(src, dst, *args, **kwargs):\n"
            f"    if str(src) == {str(self.app)!r} and '/scratch/tree/' in str(dst):\n"
            "        path = pathlib.Path(src)\n        old = path.read_bytes()\n"
            "        path.write_text('value = 2\\n')\n"
            "        try:\n            result = original(src, dst, *args, **kwargs)\n"
            "        finally:\n            path.write_bytes(old)\n"
            f"        pathlib.Path({str(trigger)!r}).touch()\n"
            "        return result\n"
            "    return original(src, dst, *args, **kwargs)\n"
            "shutil.copy2 = transient\n"
        )
        identity = {"fixture": "transient worker source"}
        with patch.dict(os.environ, {"PYTHONPATH": str(hook)}):
            with self.assertRaises(util.Paused):
                schedule.run(
                    self.project.evidence / "obligations",
                    identity,
                    lambda out: verify.scratch_run(
                        self.workspace,
                        out,
                        command=self.command,
                        timeout=30,
                        expected_source_revision=pinned,
                    ),
                    reuse_allowed=False,
                    reason="fresh",
                    current_identity=lambda: identity,
                    owned_preparation=True,
                )
        self.assertTrue(trigger.exists())
        self.assertEqual(pinned, source_snapshot.snapshot(self.workspace)["revision"])
        self.assertFalse(self.marker.exists())
