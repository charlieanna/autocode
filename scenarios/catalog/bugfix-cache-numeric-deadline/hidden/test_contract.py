import math
import unittest
from fractions import Fraction

from app import Cache


class CacheContract(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.cache = Cache(2, lambda: self.now)

    def test_exact_expiry_and_hit_does_not_extend_ttl(self):
        self.cache.put("a", 1, 4)
        self.now = 3
        self.assertEqual(self.cache.get("a"), 1)
        self.now = 4
        self.assertEqual(self.cache.get("a", "missing"), "missing")
        self.assertEqual(len(self.cache), 0)

    def test_arbitrary_integer_ttl_and_exact_integer_deadline(self):
        huge = 10 ** 1000
        self.cache.put("large", "value", huge)
        self.assertEqual(self.cache.get("large"), "value")
        self.now = huge - 1
        self.assertEqual(self.cache.get("large"), "value")
        self.now = huge
        self.assertEqual(self.cache.get("large", "expired"), "expired")
        self.assertEqual(len(self.cache), 0)

        # A large insertion time must retain the final low-order second.
        self.now = huge + 7
        self.cache.put("offset", "value", huge)
        self.now = 2 * huge + 6
        self.assertEqual(self.cache.get("offset"), "value")
        self.now = 2 * huge + 7
        self.assertEqual(self.cache.get("offset", "expired"), "expired")

    def test_arbitrary_integer_ttl_with_float_clock(self):
        self.now = 0.0
        self.cache.put("large", "value", 10 ** 1000)
        self.now = 1.0
        self.assertEqual(self.cache.get("large"), "value")
        self.assertEqual(len(self.cache), 1)

    def test_float_clock_huge_ttl_expires_at_exact_deadline(self):
        self.now = 0.0
        huge = 10 ** 1000
        self.cache.put("a", "value", huge)
        self.now = huge - 1
        self.assertEqual(self.cache.get("a"), "value")
        self.now = huge
        self.assertEqual(self.cache.get("a", "expired"), "expired")

    def test_finite_float_sum_overflow_expires_at_exact_deadline(self):
        self.now = 1e308
        self.cache.put("a", "value", 1e308)
        deadline = 2 * Fraction(1e308)
        self.now = int(deadline) - 1
        self.assertEqual(self.cache.get("a"), "value")
        self.now = int(deadline)
        self.assertEqual(self.cache.get("a", "expired"), "expired")

    def test_ordinary_float_deadline_keeps_native_rounding(self):
        self.now = 0.1
        self.cache.put("a", "value", 0.2)
        self.now = math.nextafter(0.1 + 0.2, -math.inf)
        self.assertEqual(self.cache.get("a"), "value")
        self.now = 0.1 + 0.2
        self.assertEqual(self.cache.get("a", "expired"), "expired")

    def test_expired_recent_entry_reclaimed_before_live_lru(self):
        self.cache.put("long", "keep", 100)
        self.cache.put("short", "expire", 1)
        self.now = 2
        self.cache.put("new", "new", 100)
        self.assertEqual(self.cache.get("long"), "keep")
        self.assertIsNone(self.cache.get("short"))
        self.assertEqual(len(self.cache), 2)

    def test_hit_changes_recency_but_len_does_not(self):
        self.cache.put("a", "A", 10)
        self.cache.put("b", "B", 10)
        self.assertEqual(self.cache.get("a"), "A")
        self.assertEqual(len(self.cache), 2)
        self.cache.put("c", "C", 10)
        self.assertIsNone(self.cache.get("b"))
        self.assertEqual(self.cache.get("a"), "A")

    def test_overwrite_refreshes_ttl_and_none_is_a_value(self):
        self.cache.put("a", 1, 1)
        self.now = 0.5
        self.cache.put("a", None, 5)
        self.now = 1
        sentinel = object()
        self.assertIsNone(self.cache.get("a", sentinel))
        self.now = 5.5
        self.assertIs(self.cache.get("a", sentinel), sentinel)

    def test_invalid_ttl_changes_neither_value_nor_recency(self):
        self.cache.put("a", "A", 10)
        self.cache.put("b", "B", 10)
        for ttl in (0, -1, math.inf, -math.inf, math.nan, True, "1"):
            with self.subTest(ttl=ttl), self.assertRaises(ValueError):
                self.cache.put("a", "changed", ttl)
        self.cache.put("c", "C", 10)
        self.assertIsNone(self.cache.get("a"))
        self.assertEqual(self.cache.get("b"), "B")
        for capacity in (0, -1, True, 1.5):
            with self.subTest(capacity=capacity), self.assertRaises(ValueError):
                Cache(capacity, lambda: 0)
