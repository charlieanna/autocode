from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import threading
import unittest
from app import ConflictError, JobStore

class JobContract(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "jobs.db"
        self.a, self.b = JobStore(self.path), JobStore(self.path)

    def test_payload_equivalence_conflict_and_lifecycle(self):
        payload = {"a": 1, "nested": {"x": 2, "y": [1, 2]}}
        first = self.a.submit("request", payload)
        same = self.b.submit("request", {"nested": {"y": [1, 2], "x": 2}, "a": 1})
        self.assertEqual(first, same)
        self.assertEqual(set(first), {"id", "key", "payload", "status"})
        self.assertGreater(first["id"], 0)
        with self.assertRaises(ConflictError):
            self.b.submit("request", {"a": 99})
        with self.assertRaises(ValueError):
            self.a.complete(first["id"])
        self.assertEqual(self.a.list_jobs(), [first])
        self.assertEqual(self.b.claim(), dict(first, status="running"))
        done = self.a.complete(first["id"])
        self.assertEqual(self.b.complete(first["id"]), done)
        self.assertEqual(JobStore(self.path).submit("request", payload), done)
        with self.assertRaises(KeyError):
            self.a.complete(100000)
        self.assertIsNone(self.b.claim())

    def parallel(self, function):
        barrier = threading.Barrier(2)
        def invoke(store):
            barrier.wait(timeout=5)
            return function(store)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(invoke, self.a), pool.submit(invoke, self.b)]
            return [future.result(timeout=10) for future in futures]

    def test_concurrent_submission_and_claim(self):
        submitted = self.parallel(lambda store: store.submit("one", {"x": 1}))
        self.assertEqual(submitted[0], submitted[1])
        self.assertEqual(len(self.a.list_jobs()), 1)
        second = self.a.submit("two", {"x": 2})
        claimed = self.parallel(lambda store: store.claim())
        self.assertEqual({job["id"] for job in claimed}, {submitted[0]["id"], second["id"]})
        self.assertTrue(all(job["status"] == "running" for job in claimed))
        self.assertIsNone(self.a.claim())

    def test_pending_jobs_claim_in_id_order(self):
        jobs = [self.a.submit(str(i), {"value": i}) for i in range(4)]
        self.assertEqual([self.b.claim()["id"] for _ in jobs], [job["id"] for job in jobs])

    def test_large_missing_ids_raise_key_error_without_changes(self):
        first = self.a.submit("one", {"value": 1})
        running = self.b.claim()
        for identifier in (2**63, 2**100):
            with self.subTest(identifier=identifier), self.assertRaises(KeyError):
                self.a.complete(identifier)
            self.assertEqual(JobStore(self.path).list_jobs(), [running])
        self.assertEqual(self.a.complete(first["id"])["status"], "done")
