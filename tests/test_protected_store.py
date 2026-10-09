"""Binding-preserving archive storage, crash recovery and retention ownership."""

import copy
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import autocode_protected_oracles as guard
import autocode_protected_paths as paths
import autocode_protected_store as store

from .protected_store_fixture import rewrite_archive
from .test_verify import Project


class ProtectedStorageTests(unittest.TestCase):
    def setUp(self):
        self.project = Project(
            {"src/example.test.js": 'throw new Error("original assertion");\n', ".gitignore": ".autocode/\n"}
        )
        self.addCleanup(self.project.close)
        self.root = self.project.root
        self.run = self.root / ".autocode/runs/owned"
        self.test = self.root / "src/example.test.js"
        self.test.chmod(0o751)
        self.files = {"src/example.test.js": guard.identity(self.test)}
        self.record = guard.retain(self.root, self.run, self.files, "npm test")

    def legacy(self, run=None):
        run = run or self.run
        record = copy.deepcopy(self.record)
        root = run / "protected-tests" / record["binding_hash"]
        for name in record["files"]:
            paths.copy_entry(self.root / name, root / name)
        record["root"] = str(root)
        archive = Path(str(root) + ".zip")
        archive.unlink(missing_ok=True)
        return record

    def reconcile(self, state):
        settings = state["settings"]
        guard.reconcile(
            state,
            settings,
            SimpleNamespace(run_dir=self.run),
            self.root,
            self.run,
            is_test_path=lambda _: False,
            discover_command=lambda: None,
        )

    def test_archive_is_not_another_discoverable_test_and_replay_preserves_mode(self):
        self.assertEqual([self.test], list(self.root.rglob("*.test.js")))
        original = self.test.read_bytes()
        with store.opened(self.record, self.root) as extracted:
            self.assertFalse(extracted.resolve().is_relative_to(self.root.resolve()))
            retained = extracted / "src/example.test.js"
            self.assertEqual(original, retained.read_bytes())
            self.assertEqual(0o751, retained.stat().st_mode & 0o777)
        self.assertFalse(extracted.exists())
        self.assertEqual(0o600, Path(self.record["root"]).stat().st_mode & 0o777)

    def test_compaction_preserves_binding_and_settings_without_changing_source(self):
        record = self.legacy()
        state = {"settings": {"protected_tests": record}}
        before = copy.deepcopy(state)
        self.reconcile(state)
        self.assertEqual(before, state)
        self.assertEqual(record, guard.verify_binding(record))
        self.assertEqual([self.test], list(self.root.rglob("*.test.js")))
        archive = store.archive_path(record)
        saved = archive.read_bytes()
        self.reconcile(state)
        self.assertEqual(saved, archive.read_bytes())

    def test_interrupted_compaction_is_readable_and_finishes_on_next_idle_load(self):
        record = self.legacy()
        root = Path(record["root"])
        archive = Path(str(root) + ".zip")
        store.capture(record, root, archive)
        (root / "src/example.test.js").unlink()  # crash after one original was retired
        self.assertEqual(record, guard.verify_binding(record))
        self.reconcile({"settings": {"protected_tests": record}})
        self.assertFalse(root.exists())
        self.assertEqual(record, guard.verify_binding(record))

    def test_active_runs_foreign_roots_and_unrelated_files_are_not_removed(self):
        record = self.legacy()
        state = {"settings": {"protected_tests": record}, "active_stage": {"stage": "terra"}}
        self.reconcile(state)
        self.assertTrue((Path(record["root"]) / "src/example.test.js").exists())
        self.assertIsNone(store.archive_path(record))
        foreign = self.legacy(self.root / ".autocode/runs/another")
        self.reconcile({"settings": {"protected_tests": foreign}})
        self.assertTrue((Path(foreign["root"]) / "src/example.test.js").exists())
        extra = Path(record["root"]) / "unrelated.txt"
        extra.write_text("keep me")
        self.reconcile({"settings": {"protected_tests": record}})
        self.assertEqual("keep me", extra.read_text())
        self.assertEqual(record, guard.verify_binding(record))

    def test_historical_bindings_compact_without_erasing_revision_history(self):
        old = self.legacy()
        self.test.write_text('throw new Error("new user-approved assertion");\n')
        new = guard.retain(self.root, self.run, {"src/example.test.js": guard.identity(self.test)}, "npm test")
        state = {
            "settings": {"protected_tests": new},
            "user_events": [
                {"kind": "protected_tests_revised", "previous": old, "current": new, "reason": "user decision"}
            ],
        }
        before = copy.deepcopy(state)
        self.reconcile(state)
        self.assertEqual(before, state)
        with store.opened(old, self.root) as original:
            self.assertIn("original assertion", (original / "src/example.test.js").read_text())

    def test_tampered_legacy_or_archive_never_gets_silently_recaptured(self):
        record = self.legacy()
        root = Path(record["root"])
        store.capture(record, root, Path(str(root) + ".zip"))
        (root / "src/example.test.js").write_text("pass")
        with self.assertRaisesRegex(ValueError, "bundle changed"):
            self.reconcile({"settings": {"protected_tests": record}})
        self.assertEqual("pass", (root / "src/example.test.js").read_text())
        (root / "src/example.test.js").write_bytes(self.test.read_bytes())
        rewrite_archive(record, {"src/example.test.js": b"pass"})
        with self.assertRaisesRegex(ValueError, "bundle changed"):
            self.reconcile({"settings": {"protected_tests": record}})
        self.assertTrue((root / "src/example.test.js").exists())

    def test_duplicate_members_missing_members_and_mode_tampering_are_rejected(self):
        archive = Path(self.record["root"])
        original = archive.read_bytes()
        for fault in ("duplicate", "missing", "mode"):
            with self.subTest(fault=fault):
                archive.write_bytes(original)
                with zipfile.ZipFile(archive) as bundle:
                    info = bundle.infolist()[0]
                    data = bundle.read(info.filename)
                with zipfile.ZipFile(archive, "w") as bundle:
                    if fault != "missing":
                        if fault == "mode":
                            info.external_attr ^= 0o100 << 16
                        bundle.writestr(info, data)
                        if fault == "duplicate":
                            with self.assertWarns(UserWarning):
                                bundle.writestr(info, data)
                with self.assertRaisesRegex(ValueError, "bundle changed"):
                    guard.verify_binding(self.record)

    def redirect_storage(self, record):
        directory = Path(record["root"]).parent
        foreign = self.root / "foreign-storage"
        directory.rename(foreign)
        directory.symlink_to(foreign, target_is_directory=True)
        return foreign

    def test_compaction_refuses_redirected_storage_without_removing_foreign_originals(self):
        record = self.legacy()
        foreign = self.redirect_storage(record)
        original = foreign / record["binding_hash"] / "src/example.test.js"
        before = original.read_bytes()
        with self.assertRaisesRegex(ValueError, "storage directory"):
            self.reconcile({"settings": {"protected_tests": record}})
        self.assertEqual(before, original.read_bytes())
        self.assertFalse((foreign / (record["binding_hash"] + ".zip")).exists())

    def test_capture_refuses_redirected_storage_even_when_an_archive_exists(self):
        foreign = self.redirect_storage(self.record)
        archive = foreign / Path(self.record["root"]).name
        before = archive.read_bytes()
        with self.assertRaisesRegex(ValueError, "storage directory"):
            guard.retain(self.root, self.run, self.files, "npm test")
        self.assertEqual(before, archive.read_bytes())

    def test_replay_temp_directory_inside_project_is_not_used(self):
        inside = self.root / "tmp"
        inside.mkdir()
        with patch.object(store.tempfile, "gettempdir", return_value=str(inside)):
            with store.opened(self.record, self.root) as extracted:
                self.assertFalse(extracted.resolve().is_relative_to(self.root.resolve()))
                self.assertEqual(self.test.read_bytes(), (extracted / "src/example.test.js").read_bytes())
