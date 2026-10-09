import tempfile
import unittest
from pathlib import Path

from outbox import Store


class OutboxTests(unittest.TestCase):
    def test_orders_publish_and_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'db'; store=Store(path)
            self.assertTrue(store.create_order('a',9,'key'))
            self.assertFalse(store.create_order('a',9,'key'))
            deliveries=[]
            self.assertEqual(store.publish(deliveries.append),1)
            self.assertEqual(deliveries[0]['order_id'],'a')
            store=Store(path)
            self.assertEqual(store.orders(),{'a':9}); self.assertEqual(store.pending(),[])

    def test_sink_failure_retries_same_durable_event(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'db'; store=Store(path)
            store.create_order('pending',20,'request')
            seen=[]
            def fail(event):
                seen.append(event)
                raise RuntimeError('broker unavailable')
            with self.assertRaises(RuntimeError): store.publish(fail)
            store=Store(path)
            self.assertEqual(store.pending(),seen)
            self.assertEqual(store.publish(lambda event:None),1)
