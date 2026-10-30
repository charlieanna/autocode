import tempfile
import unittest
from pathlib import Path

from leasequeue import LeaseQueue


class QueueTests(unittest.TestCase):
    def test_enqueue_claim_restart_and_ack(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "queue.db"
            q = LeaseQueue(path)
            self.assertTrue(q.enqueue("first", "work"))
            self.assertFalse(q.enqueue("first", "work"))
            item = q.claim(10, 5)
            self.assertEqual((item["id"], item["payload"], item["deadline"]), ("first", "work", 15))
            q = LeaseQueue(path)
            self.assertTrue(q.ack("first", item["token"], 12))
            self.assertEqual(q.pending(), 0)
            self.assertIsNone(q.claim(20, 5))

    def test_inputs_are_validated_at_their_boundaries(self):
        with tempfile.TemporaryDirectory() as folder:
            q = LeaseQueue(Path(folder) / "queue.db")
            with self.assertRaises(ValueError):
                q.enqueue("text-only", 5)
            self.assertTrue(q.enqueue("first", "work"))
            with self.assertRaises(ValueError):
                q.enqueue("first", "other")
            for now, lease in ((-1, 5), (True, 5), (0, 0), (0, True)):
                with self.assertRaises(ValueError):
                    q.claim(now, lease)
            item = q.claim(0, 1)
            self.assertEqual((item["id"], item["deadline"]), ("first", 1))
            with self.assertRaises(ValueError):
                q.ack("first", item["token"], -1)

    def test_expiry_at_the_deadline_fences_the_old_token(self):
        with tempfile.TemporaryDirectory() as folder:
            q = LeaseQueue(Path(folder) / "queue.db")
            q.enqueue("first", "work")
            old = q.claim(10, 5)
            self.assertIsNone(q.claim(14, 5))
            self.assertFalse(q.ack("first", "not-the-token", 11))
            self.assertFalse(q.ack("first", old["token"], 15))
            new = q.claim(15, 5)
            self.assertEqual(("first", 20), (new["id"], new["deadline"]))
            self.assertNotEqual(old["token"], new["token"])
            self.assertFalse(q.ack("first", old["token"], 16))
            self.assertEqual(q.pending(), 1)

    def test_nack_releases_for_immediate_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            q = LeaseQueue(Path(folder) / "queue.db")
            q.enqueue("first", "work")
            item = q.claim(10, 5)
            self.assertTrue(q.nack("first", item["token"], 11))
            self.assertFalse(q.nack("first", item["token"], 11))
            again = q.claim(11, 5)
            self.assertEqual("first", again["id"])
            self.assertTrue(q.ack("first", again["token"], 12))
            self.assertEqual(q.pending(), 0)
