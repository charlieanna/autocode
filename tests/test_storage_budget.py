"""Storage budget (#971): project a run's copy growth against free space before spending.

The floor refusal (not one source copy fits) and the measured ~3x warning band
must fire before any model budget is consumed, and doctor must report the same
numbers (issue #971, fix direction 2).
"""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import autocode_build_loop as build_loop
import autocode_doctor as doctor
import autocode_storage_budget as storage_budget


def fake_disk(free, total=10_000_000_000):
    return lambda root: SimpleNamespace(free=free, total=total)


class WorkspaceBytesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

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


class VerdictTests(unittest.TestCase):
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
