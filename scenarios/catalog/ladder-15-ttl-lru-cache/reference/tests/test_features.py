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

    def test_large_integer_ttl_with_integer_and_float_clocks(self):
        huge = 10 ** 1000
        now = [0]
        cache = Cache(1, lambda: now[0])
        cache.put("a", "value", huge)
        now[0] = huge - 1
        self.assertEqual(cache.get("a"), "value")
        now[0] = huge
        self.assertIsNone(cache.get("a"))

        now[0] = 0.0
        cache = Cache(1, lambda: now[0])
        cache.put("a", "value", huge)
        now[0] = 1.0
        self.assertEqual(cache.get("a"), "value")
