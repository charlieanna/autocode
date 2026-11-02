"""Storage budget (#971): project a run's copy growth against free space before spending.

The floor refusal (not one source copy fits) and the measured ~3x warning band
must fire before any model budget is consumed, and doctor must report the same
numbers (issue #971, fix direction 2).
"""

import contextlib
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import autocode_build_loop as build_loop
import autocode_doctor as doctor
import autocode_storage_budget as storage_budget
import autocode_task_preflight as task_preflight
import autocode_util as util


def fake_disk(free, total=10_000_000_000):
    return lambda root: SimpleNamespace(free=free, total=total)


class WorkspaceBytesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()

    def test_counts_source_bytes_only(self):
        (self.root / "src").mkdir()
        (self.root / "src" / "a.py").write_bytes(b"x" * 100)
        (self.root / "src" / "cache.pyc").write_bytes(b"x" * 50)
        (self.root / "src" / "__pycache__").mkdir()
        (self.root / "src" / "__pycache__" / "b.py").write_bytes(b"x" * 200)
        (self.root / ".git").mkdir()
        (self.root / ".git" / "objects").mkdir()
        (self.root / ".git" / "objects" / "blob").write_bytes(b"x" * 1000)
        (self.root / ".autocode").mkdir()
        (self.root / ".autocode" / "state.json").write_bytes(b"x" * 5000)
        (self.root / "link").symlink_to(self.root / "src" / "a.py")
        self.assertEqual(storage_budget.workspace_bytes(self.root), 100)

    def test_projection_multiplies_the_workspace_and_compares_free_space(self):
        (self.root / "a.py").write_bytes(b"x" * 1000)
        budget = storage_budget.projection(self.root, multiplier=3, disk_usage=fake_disk(free=2_500))
        self.assertEqual(budget["workspace_bytes"], 1000)
        self.assertEqual(budget["projected_bytes"], 3000)
        self.assertEqual(budget["free_bytes"], 2_500)

    def test_excluded_trees_are_never_traversed(self):
        excluded = [self.root / name for name in (".git", ".autocode", ".autocode-ui", ".scenario-runs", "__pycache__")]
        excluded.append(self.root / "src" / "__pycache__")
        excluded.extend(self.root / "nested" / name for name in (".git", ".autocode", ".autocode-ui"))
        for directory in excluded:
            directory.mkdir(parents=True)
            (directory / "large-history").write_bytes(b"ignored")
        (self.root / "src" / "main.py").write_bytes(b"source")
        scandir = os.scandir

        def guarded_scandir(path):
            self.assertNotIn(Path(path), excluded, "storage estimate traversed an excluded tree")
            return scandir(path)

        with mock.patch.object(os, "scandir", side_effect=guarded_scandir):
            self.assertEqual(storage_budget.workspace_bytes(self.root), len(b"source"))

    def test_directory_symlinks_are_not_followed_and_dependencies_are_counted(self):
        dependencies = self.root / "node_modules"
        dependencies.mkdir()
        (dependencies / "package.js").write_bytes(b"dependency")
        (self.root / "linked-dependencies").symlink_to(dependencies, target_is_directory=True)
        (self.root / "loop").symlink_to(self.root, target_is_directory=True)
        (self.root / "broken").symlink_to(self.root / "missing")
        self.assertEqual(storage_budget.workspace_bytes(self.root), len(b"dependency"))

    def test_registered_nested_worktrees_are_not_source_but_nested_projects_are(self):
        def git(*args):
            subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True)

        git("init", "-q")
        git("-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "--allow-empty", "-qm", "seed")
        worktree = self.root / "agent checkouts" / "other\ncheckout"
        git("worktree", "add", "--detach", str(worktree), "HEAD")
        (worktree / "duplicate.py").write_bytes(b"duplicate" * 100)
        nested = self.root / "component"
        nested.mkdir()
        subprocess.run(["git", "-C", str(nested), "init", "-q"], check=True, capture_output=True)
        (nested / "source.py").write_bytes(b"source")
        scandir = os.scandir

        def guarded_scandir(path):
            self.assertNotEqual(Path(path), worktree, "storage estimate traversed another checkout")
            return scandir(path)

        with mock.patch.object(os, "scandir", side_effect=guarded_scandir):
            self.assertEqual(storage_budget.workspace_bytes(self.root), len(b"source"))

    def test_workspace_without_git_still_counts_files(self):
        (self.root / "main.py").write_bytes(b"source")
        self.assertEqual(storage_budget.workspace_bytes(self.root), len(b"source"))
        for dot in ("not a git directory", "gitdir: /missing/checkout/.git\n"):
            with self.subTest(dot=dot.splitlines()[0]):
                (self.root / ".git").write_text(dot)
                self.assertEqual(storage_budget.workspace_bytes(self.root), len(b"source"))


class VerdictTests(unittest.TestCase):
    def test_dispatch_without_source_copy_does_not_scan_source_and_refuses_full_storage(self):
        with (
            tempfile.TemporaryDirectory() as root,
            mock.patch.object(storage_budget, "workspace_bytes") as size,
            mock.patch.object(storage_budget.shutil, "disk_usage", side_effect=fake_disk(free=999)),
        ):
            self.assertEqual("", storage_budget.dispatch_refusal(root, root, source_copy=False))
            size.assert_not_called()
            with mock.patch.object(storage_budget.shutil, "disk_usage", side_effect=fake_disk(free=0)):
                self.assertIn("storage", storage_budget.dispatch_refusal(root, root, source_copy=False))

    def test_requirements_chat_needs_writable_storage_but_not_a_source_copy(self):
        state = {"settings": {"spec_first": True}, "next_stage": "requirements_gather"}
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root)
            (workspace / "large.py").write_bytes(b"x" * 1000)
            with mock.patch.object(storage_budget.shutil, "disk_usage", side_effect=fake_disk(free=999)):
                task_preflight.guard(state, workspace, workspace / "run")
                state["next_stage"] = "astra_discovery"
                with self.assertRaisesRegex(util.Paused, "source copy"):
                    task_preflight.guard(state, workspace, workspace / "run")
            state["next_stage"] = "requirements_gather"
            with mock.patch.object(storage_budget.shutil, "disk_usage", side_effect=fake_disk(free=0)):
                with self.assertRaisesRegex(util.Paused, "storage"):
                    task_preflight.guard(state, workspace, workspace / "run")

    def test_dispatch_checks_workspace_run_and_temporary_storage_each_time(self):
        with tempfile.TemporaryDirectory() as root:
            workspace, run, scratch = (Path(root).resolve() / name for name in ("project", "runs", "scratch"))
            for path in (workspace, run, scratch):
                path.mkdir()
            (workspace / "source.py").write_bytes(b"x" * 1000)
            free = {workspace: 10000, run: 10000, scratch: 10000}

            def disk(path):
                return SimpleNamespace(free=free[Path(path)], total=100000)

            with (
                mock.patch.object(tempfile, "gettempdir", return_value=str(scratch)),
                mock.patch.object(storage_budget.shutil, "disk_usage", side_effect=disk),
            ):
                self.assertEqual("", storage_budget.dispatch_refusal(workspace, run))
                for path in (workspace, run, scratch):
                    free[path] = 999
                    reason = storage_budget.dispatch_refusal(workspace, run)
                    self.assertIn(str(path), reason)
                    self.assertIn("1000 B", reason)
                    free[path] = 10000
                self.assertEqual("", storage_budget.dispatch_refusal(workspace, run))

    def test_storage_gate_applies_without_an_operator_manifest_and_before_reuse(self):
        for settings in ({}, {"task_preflight": {"body": {}}}):
            with (
                self.subTest(settings=settings),
                mock.patch.object(
                    storage_budget, "dispatch_refusal", return_value="scratch is full", create=True
                ) as guard,
            ):
                with self.assertRaisesRegex(util.Paused, "scratch is full") as caught:
                    task_preflight.guard({"settings": settings}, Path("/workspace"), Path("/run"))
                self.assertEqual("PAUSED_TASK_PREFLIGHT", caught.exception.status)
                guard.assert_called_once_with(Path("/workspace"), Path("/run"), source_copy=True)

    def budget(self, size=1000, free=5000, multiplier=3):
        return {
            "workspace_bytes": size,
            "multiplier": multiplier,
            "projected_bytes": size * multiplier,
            "free_bytes": free,
            "total_bytes": 100_000,
        }

    def test_refusal_names_the_doomed_floor(self):
        reason = storage_budget.refusal(self.budget(size=1000, free=999))
        self.assertIn("even one source copy", reason)
        self.assertIn("1000 B", reason)
        self.assertEqual(storage_budget.refusal(self.budget(size=1000, free=1000)), "")

    def test_warning_names_the_projection_below_the_measured_growth(self):
        note = storage_budget.warning(self.budget(size=1000, free=2999))
        self.assertIn("3x", note)
        self.assertIn("2.9 KiB", note)
        self.assertEqual(storage_budget.warning(self.budget(size=1000, free=3000)), "")

    def test_human_rounds_to_operator_units(self):
        self.assertEqual(storage_budget.human(1024 * 1024 * 5), "5.0 MiB")
        self.assertEqual(storage_budget.human(1024 * 1024 * 2048), "2.0 GiB")


class DoctorCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "a.py").write_bytes(b"x" * 1000)

    def test_ok_warn_and_missing_statuses(self):
        ok = doctor.storage_check(self.root, disk_usage=fake_disk(free=10_000))
        self.assertEqual(ok.status, doctor.OK)
        self.assertIn(storage_budget.human(3000), ok.detail)
        warn = doctor.storage_check(self.root, disk_usage=fake_disk(free=2_000))
        self.assertEqual(warn.status, doctor.WARN)
        self.assertTrue(warn.fix)
        missing = doctor.storage_check(self.root, disk_usage=fake_disk(free=500))
        self.assertEqual(missing.status, doctor.MISSING)
        self.assertTrue(missing.fix)
        self.assertFalse(doctor.passed([missing]))
        self.assertTrue(doctor.passed([warn]))


class BuildLoopFloorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_requirements_chat_does_not_scan_the_workspace(self):
        state = {"settings": {"spec_first": True}, "next_stage": "requirements_gather"}
        runner = SimpleNamespace(orchestrator=SimpleNamespace(drive=mock.Mock(), LoopExit=RuntimeError))
        with (
            mock.patch.object(build_loop.storage_budget, "projection") as projection,
            mock.patch.object(build_loop.checkout_lock, "exclusive", return_value=contextlib.nullcontext()),
        ):
            build_loop.run(runner, SimpleNamespace(unit=None), state, self.root / "state.json", self.root, self.root)
        projection.assert_not_called()
        runner.orchestrator.drive.assert_called_once()

    def test_chat_transition_checks_storage_before_dispatching_planning(self):
        state = {"settings": {"spec_first": True}, "next_stage": "requirements_gather"}

        class LoopExit(Exception):
            def __init__(self, code):
                self.code = code

        def drive(current, dispatch, *, before, **kwargs):
            current["next_stage"] = "astra_discovery"
            before(current)
            self.fail("planning dispatched without the storage refusal")

        runner = SimpleNamespace(orchestrator=SimpleNamespace(drive=drive, LoopExit=LoopExit))
        floor = {"workspace_bytes": 1000, "free_bytes": 1}
        with (
            mock.patch.object(build_loop.storage_budget, "projection", return_value=floor) as projection,
            mock.patch.object(build_loop.checkout_lock, "exclusive", return_value=contextlib.nullcontext()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            code = build_loop.run(
                runner, SimpleNamespace(unit=None), state, self.root / "state.json", self.root, self.root
            )
        self.assertEqual(code, 2)
        projection.assert_called_once_with(self.root)

    def test_a_doomed_filesystem_is_refused_before_the_loop(self):
        floor = {
            "workspace_bytes": 1000,
            "multiplier": 3,
            "projected_bytes": 3000,
            "free_bytes": 100,
            "total_bytes": 200,
        }
        with mock.patch.object(build_loop.storage_budget, "projection", return_value=floor):
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = build_loop.run(None, None, {}, self.root / "state.json", self.root, self.root)
        self.assertEqual(code, 2)
        self.assertIn("Cannot start: preparing even one source copy", stderr.getvalue())

    def test_the_warning_band_starts_the_loop_with_the_numbers(self):
        band = {
            "workspace_bytes": 1000,
            "multiplier": 3,
            "projected_bytes": 3000,
            "free_bytes": 2000,
            "total_bytes": 100_000,
        }

        class LoopExit(Exception):
            pass

        orchestrator = SimpleNamespace(drive=mock.Mock(), LoopExit=LoopExit)
        runner = SimpleNamespace(orchestrator=orchestrator)
        args = SimpleNamespace(unit=None)
        with (
            mock.patch.object(build_loop.storage_budget, "projection", return_value=band),
            mock.patch.object(build_loop.checkout_lock, "exclusive", return_value=contextlib.nullcontext()),
        ):
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = build_loop.run(runner, args, {}, self.root / "state.json", self.root, self.root)
        self.assertIsNone(code)
        self.assertIn("warning: low disk space for a run", stderr.getvalue())
        runner.orchestrator.drive.assert_called_once()


if __name__ == "__main__":
    unittest.main()
