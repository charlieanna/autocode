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
