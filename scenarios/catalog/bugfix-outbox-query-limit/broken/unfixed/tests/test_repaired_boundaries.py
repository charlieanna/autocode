import tempfile
import unittest
from pathlib import Path

from outbox import Store


class LargeQueryLimits(unittest.TestCase):
    def test_t1_large_limit_survives_reopen_and_publish(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "orders.db"
            store = Store(path)
            for i in range(3):
                store.create_order(str(i), i + 1, str(i))
            expected = store.pending()
            reopened = Store(path)
            self.assertEqual(expected, reopened.pending(2**120))
            delivered = []
            self.assertEqual(3, reopened.publish(delivered.append, limit=10**5000))
            self.assertEqual(expected, delivered)
            self.assertEqual([], Store(path).pending(2**120))
