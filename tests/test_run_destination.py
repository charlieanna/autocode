"""Exact fresh destinations cannot overwrite or redirect an existing run."""

import tempfile
import unittest
from pathlib import Path

import autocode_run_destination as destination


class FreshDestinationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.target = self.workspace / ".autocode" / "runs" / "recording-replay"

    def test_allocates_the_exact_new_directory(self):
        self.assertEqual(self.target, destination.reserve(self.workspace, self.target))
        self.assertTrue(self.target.is_dir())
        self.assertEqual([], list(self.target.iterdir()))

    def test_an_existing_empty_or_saved_run_is_never_reused(self):
        self.target.mkdir(parents=True)
        for saved in (False, True):
            with self.subTest(saved=saved):
                if saved:
                    (self.target / "evidence.jsonl").write_bytes(b"retained evidence\n")
                before = {p.name: p.read_bytes() for p in self.target.iterdir()}
                with self.assertRaises(FileExistsError):
                    destination.reserve(self.workspace, self.target)
                self.assertEqual(before, {p.name: p.read_bytes() for p in self.target.iterdir()})

    def test_external_and_nested_destinations_are_refused_before_writes(self):
        for path in (self.root / "foreign", self.target / "nested", self.workspace / ".autocode" / "runs"):
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    destination.reserve(self.workspace, path)
                self.assertFalse((self.workspace / ".autocode").exists())
        self.assertFalse((self.root / "foreign").exists())

    def test_traversal_cannot_choose_another_directory(self):
        path = self.target.parent / ".." / "runs" / "other"
        with self.assertRaises(ValueError):
            destination.reserve(self.workspace, path)
        self.assertFalse((self.workspace / ".autocode").exists())

    def test_symlinked_store_cannot_write_into_a_foreign_directory(self):
        foreign = self.root / "foreign"
        foreign.mkdir()
        (self.workspace / ".autocode").symlink_to(foreign, target_is_directory=True)
        with self.assertRaises(ValueError):
            destination.reserve(self.workspace, self.target)
        self.assertEqual([], list(foreign.iterdir()))

    def test_symlinked_destination_cannot_replace_earlier_evidence(self):
        foreign = self.root / "foreign"
        foreign.mkdir()
        (foreign / "evidence.jsonl").write_bytes(b"original\n")
        self.target.parent.mkdir(parents=True)
        self.target.symlink_to(foreign, target_is_directory=True)
        with self.assertRaises(ValueError):
            destination.reserve(self.workspace, self.target)
        self.assertEqual(b"original\n", (foreign / "evidence.jsonl").read_bytes())

    def test_a_regular_file_cannot_be_used_as_the_run_store(self):
        (self.workspace / ".autocode").write_bytes(b"keep\n")
        with self.assertRaises(OSError):
            destination.reserve(self.workspace, self.target)
        self.assertEqual(b"keep\n", (self.workspace / ".autocode").read_bytes())
