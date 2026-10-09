from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import threading
import unittest
from app import ConflictError, DocumentStore

class DocumentContract(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "docs.db"
        self.a, self.b = DocumentStore(self.path), DocumentStore(self.path)

    def test_conflicts_preserve_data_across_clients(self):
        self.assertTrue(issubclass(ConflictError, ValueError))
        first = self.a.create("x'; --", "héllo")
        self.assertEqual(first, {"key": "x'; --", "body": "héllo", "version": 1})
        with self.assertRaises(ConflictError):
            self.b.create("x'; --", "replacement")
        updated = self.b.update("x'; --", "changed", 1)
        with self.assertRaises(ConflictError):
            self.a.update("x'; --", "stale data", 1)
        self.assertEqual(self.a.read("x'; --"), updated)
        with self.assertRaises(KeyError):
            self.a.update("missing", "text", 1)
        with self.assertRaises(KeyError):
            DocumentStore(self.path.with_name("other.db")).read("x'; --")

    def test_same_version_has_one_winner(self):
        self.a.create("race", "start")
        barrier = threading.Barrier(2)
        def update(store, body):
            barrier.wait(timeout=5)
            try:
                return store.update("race", body, 1)
            except ConflictError:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(update, self.a, "a"), pool.submit(update, self.b, "b")]
            winners = [value for value in (future.result(timeout=10) for future in futures) if value is not None]
        self.assertEqual(len(winners), 1)
        self.assertEqual(self.a.read("race"), winners[0])
        self.assertEqual(winners[0]["version"], 2)

    def test_invalid_versions_do_not_change_document(self):
        original = self.a.create("a", "body")
        for version in (True, 0, -1, 1.5, "1"):
            with self.subTest(version=version), self.assertRaises(ValueError):
                self.a.update("a", "changed", version)
        self.assertEqual(self.a.read("a"), original)
        with self.assertRaises(ValueError):
            self.a.create("", "body")
        with self.assertRaises(ValueError):
            self.a.create("bad", None)

    def test_large_expected_versions_keep_conflict_and_missing_contract(self):
        original = self.a.create("existing", "unchanged")
        for version in (2**63, 2**100):
            with self.subTest(version=version):
                with self.assertRaises(ConflictError):
                    self.b.update("existing", "replacement", version)
                with self.assertRaises(KeyError):
                    self.b.update("absent", "replacement", version)
                self.assertEqual(DocumentStore(self.path).read("existing"), original)
        self.assertEqual(self.b.update("existing", "new", 1)["version"], 2)
