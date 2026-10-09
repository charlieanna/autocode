import unittest
from app import Cache

class CacheTests(unittest.TestCase):
    def test_store_hit_and_expiry(self):
        now = [0]
        cache = Cache(2, lambda: now[0])
        cache.put("a", "value", 2)
        self.assertEqual(cache.get("a"), "value")
        now[0] = 3
        self.assertEqual(cache.get("a", "missing"), "missing")
        self.assertEqual(len(cache), 0)
