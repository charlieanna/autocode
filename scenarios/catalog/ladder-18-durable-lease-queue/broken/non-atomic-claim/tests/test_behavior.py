import tempfile
import unittest
from pathlib import Path
from leasequeue import LeaseQueue

class QueueTests(unittest.TestCase):
    def test_enqueue_claim_restart_and_ack(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'queue.db'; q=LeaseQueue(path)
            self.assertTrue(q.enqueue('first','work'))
            self.assertFalse(q.enqueue('first','work'))
            item=q.claim(10,5)
            self.assertEqual((item['id'],item['payload'],item['deadline']),('first','work',15))
            q=LeaseQueue(path)
            self.assertTrue(q.ack('first',item['token'],12))
            self.assertEqual(q.pending(),0)
            self.assertIsNone(q.claim(20,5))
