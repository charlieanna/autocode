"""Real filesystem custody for one fresh retained-blob read."""

import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_protected_blob_read as blobs


class ProtectedBlobReadTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.directory = self.root / "blobs"
        self.directory.mkdir()
        self.raw = b"original protected bytes\n"
        self.name = hashlib.sha256(self.raw).hexdigest()
        self.path = self.directory / self.name
        self.path.write_bytes(self.raw)

    def test_actual_bytes_are_fresh_on_each_call_and_unique_blob_is_read_once(self):
        original = os.open
        opened = []

        def observed(name, *args, **kwargs):
            if kwargs.get("dir_fd") is not None:
                opened.append(name)
            return original(name, *args, **kwargs)

        with patch.object(blobs.os, "open", side_effect=observed):
            self.assertEqual({self.name: self.raw}, blobs.read(self.directory, [self.name, self.name]))
        self.assertEqual([self.name], opened)
        self.path.write_bytes(b"changed current bytes")
        with self.assertRaises(ValueError):
            blobs.read(self.directory, [self.name])

    def test_symlink_leaf_and_aliased_directory_refuse_without_following_target(self):
        target = self.root / "outside"
        target.write_bytes(self.raw)
        self.path.unlink()
        self.path.symlink_to(target)
        with self.assertRaises(OSError):
            blobs.read(self.directory, [self.name])
        self.path.unlink()
        self.path.write_bytes(self.raw)
        alias = self.root / "alias"
        alias.symlink_to(self.directory, target_is_directory=True)
        with self.assertRaises(ValueError):
            blobs.read(alias, [self.name])
        self.assertEqual(self.raw, target.read_bytes())

    def test_leaf_replacement_after_actual_open_is_detected_even_with_identical_bytes(self):
        original = os.read
        replaced = []

        def replace_after_read(descriptor, count):
            result = original(descriptor, count)
            if result and not replaced:
                replaced.append(True)
                self.path.unlink()
                self.path.write_bytes(self.raw)
            return result

        with patch.object(blobs.os, "read", side_effect=replace_after_read):
            with self.assertRaises(ValueError):
                blobs.read(self.directory, [self.name])
        self.assertEqual([True], replaced)
        self.assertEqual(self.raw, self.path.read_bytes())

    def test_namespace_create_remove_is_detected(self):
        original = os.read
        changed = []

        def change_after_read(descriptor, count):
            result = original(descriptor, count)
            if result and not changed:
                changed.append(True)
                extra = self.directory / "transient"
                extra.write_bytes(b"transient")
                extra.unlink()
            return result

        with patch.object(blobs.os, "read", side_effect=change_after_read):
            with self.assertRaises(ValueError):
                blobs.read(self.directory, [self.name])
        self.assertEqual([True], changed)

    def test_directory_replacement_after_actual_open_is_detected(self):
        original = os.read
        replaced = []

        def replace_after_read(descriptor, count):
            result = original(descriptor, count)
            if result and not replaced:
                replaced.append(True)
                self.directory.rename(self.root / "retained-original")
                self.directory.mkdir()
                self.path.write_bytes(self.raw)
            return result

        with patch.object(blobs.os, "read", side_effect=replace_after_read):
            with self.assertRaises(ValueError):
                blobs.read(self.directory, [self.name])
        self.assertEqual([True], replaced)
        self.assertEqual(self.raw, (self.root / "retained-original" / self.name).read_bytes())
        self.assertEqual(self.raw, self.path.read_bytes())

    def test_nonregular_or_missing_leaf_and_path_traversal_are_refused(self):
        self.path.unlink()
        self.path.mkdir()
        with self.assertRaises(ValueError):
            blobs.read(self.directory, [self.name])
        with self.assertRaises((OSError, ValueError)):
            blobs.read(self.directory, ["0" * 64])
        for name in ("../outside", "state.json", "/tmp/outside", "A" * 64, {"not": "a leaf"}):
            with self.subTest(name=name), self.assertRaises(ValueError):
                blobs.read(self.directory, [name])
        with self.assertRaises(ValueError):
            blobs.read(self.root / "blobs" / "..", [self.name])
