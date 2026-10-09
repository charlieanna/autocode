import unittest

from app import Cache


class NumericDeadlines(unittest.TestCase):
    def test_t1_large_integer_ttl_with_float_clock(self):
        now = [0.0]
        cache = Cache(2, lambda: now[0])
        cache.put("large", "value", 10**1000)
        self.assertEqual("value", cache.get("large"))
        now[0] = 10**1000
        self.assertEqual("expired", cache.get("large", "expired"))

    def test_t2_finite_float_overflow_expires_at_exact_sum(self):
        now = [1e308]
        cache = Cache(2, lambda: now[0])
        cache.put("large", "value", 1e308)
        self.assertEqual("value", cache.get("large"))
        now[0] = 2 * int(1e308)
        self.assertEqual("expired", cache.get("large", "expired"))
