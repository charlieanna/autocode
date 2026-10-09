import tempfile
import unittest
from pathlib import Path

from app import JobStore


class JobTests(unittest.TestCase):
    def test_submit_claim_complete(self):
        with tempfile.TemporaryDirectory() as folder:
            store = JobStore(Path(folder) / "jobs.db")
            first = store.submit("request", {"task": "export"})
            self.assertEqual(store.submit("request", {"task": "export"}), first)
            self.assertEqual(store.claim()["id"], first["id"])
            self.assertEqual(store.complete(first["id"])["status"], "done")
            self.assertIsNone(store.claim())

    def test_large_missing_id_preserves_pending_job(self):
        with tempfile.TemporaryDirectory() as folder:
            store = JobStore(Path(folder) / "jobs.db")
            first = store.submit("request", {"task": "export"})
            with self.assertRaises(KeyError):
                store.complete(2**80)
            self.assertEqual(store.list_jobs(), [first])
