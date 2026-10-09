import tempfile
import unittest
from pathlib import Path

from app import ConflictError, DocumentStore


class DocumentTests(unittest.TestCase):
    def test_create_update_reopen(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "docs.db"
            store = DocumentStore(path)
            self.assertEqual(store.create("a", "first")["version"], 1)
            changed = store.update("a", "second", 1)
            self.assertEqual(changed, {"key": "a", "body": "second", "version": 2})
            self.assertEqual(DocumentStore(path).read("a"), changed)

    def test_large_stale_version_is_a_conflict(self):
        with tempfile.TemporaryDirectory() as folder:
            store = DocumentStore(Path(folder) / "docs.db")
            original = store.create("a", "first")
            with self.assertRaises(ConflictError):
                store.update("a", "replacement", 2**80)
            with self.assertRaises(KeyError):
                store.update("missing", "replacement", 2**80)
            self.assertEqual(store.read("a"), original)
