import tempfile
import unittest
from pathlib import Path

from outbox import Store


class OutboxTests(unittest.TestCase):
    def test_orders_publish_and_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "db"
            store = Store(path)
            self.assertTrue(store.create_order("a", 9, "key"))
            self.assertFalse(store.create_order("a", 9, "key"))
            deliveries = []
            self.assertEqual(store.publish(deliveries.append), 1)
            self.assertEqual(deliveries[0]["order_id"], "a")
            store = Store(path)
            self.assertEqual(store.orders(), {"a": 9})
            self.assertEqual(store.pending(), [])
