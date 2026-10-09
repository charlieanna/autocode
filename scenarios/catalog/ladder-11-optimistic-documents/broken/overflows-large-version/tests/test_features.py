from pathlib import Path
import tempfile
import unittest
from app import DocumentStore

class DocumentTests(unittest.TestCase):
    def test_create_update_reopen(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "docs.db"
            store = DocumentStore(path)
            self.assertEqual(store.create("a", "first")["version"], 1)
            changed = store.update("a", "second", 1)
            self.assertEqual(changed, {"key": "a", "body": "second", "version": 2})
            self.assertEqual(DocumentStore(path).read("a"), changed)
