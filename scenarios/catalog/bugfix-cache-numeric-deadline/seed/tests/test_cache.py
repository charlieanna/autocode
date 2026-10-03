import subprocess
import sys
import unittest

from app import Cache


class FakeClock:
    def __init__(self, now=0):
        self.now = now

    def __call__(self):
        return self.now


class CacheTests(unittest.TestCase):
    def test_ac1_public_cache_operations(self):
        clock = FakeClock()
        cache = Cache(2, clock)

        cache.put("a", "alpha", 5)

        self.assertEqual(cache.get("a"), "alpha")
        self.assertEqual(len(cache), 1)

    def test_ac2_invalid_capacity_raises_valueerror(self):
        clock = FakeClock()

        for capacity in (0, -1, True):
            with self.subTest(capacity=capacity):
                with self.assertRaises(ValueError):
                    Cache(capacity, clock)

    def test_ac4_entry_expires_at_inclusive_deadline(self):
        clock = FakeClock(10)
        cache = Cache(2, clock)
        cache.put("a", "alpha", 5)

        clock.now = 15

        self.assertEqual(cache.get("a", "missing"), "missing")
        self.assertEqual(len(cache), 0)

    def test_ac5_live_get_updates_lru_without_extending_ttl(self):
        clock = FakeClock()
        cache = Cache(2, clock)
        cache.put("a", "alpha", 5)
        cache.put("b", "beta", 10)
        cache.get("a")
        cache.put("c", "gamma", 10)

        clock.now = 5

        self.assertEqual(cache.get("a", "missing"), "missing")
        self.assertEqual(cache.get("c"), "gamma")

    def test_ac6_reclaims_expired_before_live_lru_eviction(self):
        clock = FakeClock()
        cache = Cache(2, clock)
        cache.put("a", "alpha", 1)
        cache.put("b", "beta", 10)
        clock.now = 1
        cache.put("c", "gamma", 10)

        self.assertEqual(cache.get("a", "missing"), "missing")
        self.assertEqual(cache.get("b"), "beta")
        self.assertEqual(cache.get("c"), "gamma")

        clock = FakeClock()
        cache = Cache(2, clock)
        cache.put("a", "alpha", 10)
        cache.put("b", "beta", 10)
        cache.put("c", "gamma", 10)

        self.assertEqual(cache.get("a", "missing"), "missing")

    def test_ac7_len_preserves_lru_and_none_is_live_value(self):
        clock = FakeClock()
        cache = Cache(2, clock)
        cache.put("a", "alpha", 10)
        cache.put("b", "beta", 10)

        self.assertEqual(len(cache), 2)
        cache.put("c", "gamma", 10)

        self.assertEqual(cache.get("a", "missing"), "missing")
        self.assertEqual(cache.get("b"), "beta")

        cache.put("n", None, 5)

        self.assertIsNone(cache.get("n", "missing"))

    def test_ac8_unbounded_integer_capacity_and_ttl(self):
        for limit in (2**63 - 1, 2**63, 10**5000):
            with self.subTest(limit=limit):
                clock = FakeClock()
                cache = Cache(limit, clock)
                cache.put("a", "alpha", limit)

                self.assertEqual(cache.get("a", "missing"), "alpha")
                clock.now = limit
                self.assertEqual(cache.get("a", "missing"), "missing")

    def test_ac10_import_has_no_stdout(self):
        result = subprocess.run([sys.executable, "-c", "import app"], capture_output=True, text=True, timeout=5)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_ac12_invalid_ttl_preserves_value_and_lru(self):
        for ttl in (0, -1, True, float("inf"), "5"):
            with self.subTest(ttl=ttl):
                clock = FakeClock()
                cache = Cache(2, clock)
                cache.put("a", "alpha", 10)
                cache.put("b", "beta", 10)

                with self.assertRaises(ValueError):
                    cache.put("a", "replacement", ttl)

                cache.put("c", "gamma", 10)
                self.assertEqual(cache.get("a", "missing"), "missing")
                self.assertEqual(cache.get("b"), "beta")
                self.assertEqual(cache.get("c"), "gamma")

                cache = Cache(2, clock)
                cache.put("a", "alpha", 10)
                cache.put("b", "beta", 10)

                with self.assertRaises(ValueError):
                    cache.put("a", "replacement", ttl)

                self.assertEqual(cache.get("a"), "alpha")

    def test_ac13_get_lru_and_overwrite_deadline(self):
        clock = FakeClock()
        cache = Cache(2, clock)
        cache.put("a", "alpha", 10)
        cache.put("b", "beta", 10)
        cache.get("a")
        cache.put("c", "gamma", 10)

        self.assertEqual(cache.get("b", "missing"), "missing")
        self.assertEqual(cache.get("a"), "alpha")
        self.assertEqual(cache.get("c"), "gamma")

        clock = FakeClock()
        cache = Cache(2, clock)
        cache.put("a", "alpha", 5)
        cache.put("b", "beta", 10)
        clock.now = 3
        cache.put("a", "replacement", 5)
        cache.put("c", "gamma", 10)

        self.assertEqual(cache.get("b", "missing"), "missing")
        clock.now = 5
        self.assertEqual(cache.get("a"), "replacement")
        clock.now = 8
        self.assertEqual(cache.get("a", "missing"), "missing")

    def test_ac14_reclaims_mru_expired_before_lru_eviction(self):
        clock = FakeClock()
        cache = Cache(2, clock)
        cache.put("a", "alpha", 10)
        cache.put("b", "beta", 1)
        clock.now = 1
        cache.put("c", "gamma", 10)

        self.assertEqual(cache.get("a"), "alpha")
        self.assertEqual(cache.get("b", "missing"), "missing")
        self.assertEqual(cache.get("c"), "gamma")

    def test_ac15_float_and_invalid_numeric_boundaries(self):
        clock = FakeClock()
        cache = Cache(2, clock)
        cache.put("fraction", "value", 0.5)

        clock.now = 0.499
        self.assertEqual(cache.get("fraction", "missing"), "value")
        clock.now = 0.5
        self.assertEqual(cache.get("fraction", "missing"), "missing")

        cache.put("a", "alpha", 10)
        for ttl in (float("nan"), float("-inf"), -(10**5000)):
            with self.subTest(ttl=ttl):
                with self.assertRaises(ValueError):
                    cache.put("a", "replacement", ttl)
                self.assertEqual(cache.get("a"), "alpha")

        for capacity in (1.0, "2", None, -(10**5000)):
            with self.subTest(capacity=capacity):
                with self.assertRaises(ValueError):
                    Cache(capacity, clock)

    def test_ac16_each_invalid_ttl_preserves_value_and_lru(self):
        for ttl in (0, -1, True, float("inf"), float("nan"), float("-inf"), "5", -(10**5000)):
            with self.subTest(ttl=ttl):
                clock = FakeClock()
                cache = Cache(2, clock)
                cache.put("a", "alpha", 10)
                cache.put("b", "beta", 10)

                with self.assertRaises(ValueError):
                    cache.put("a", "replacement", ttl)
                self.assertEqual(cache.get("a"), "alpha")

                cache = Cache(2, clock)
                cache.put("a", "alpha", 10)
                cache.put("b", "beta", 10)

                with self.assertRaises(ValueError):
                    cache.put("a", "replacement", ttl)
                cache.put("c", "gamma", 10)

                self.assertEqual(cache.get("a", "missing"), "missing")
                self.assertEqual(cache.get("b"), "beta")
                self.assertEqual(cache.get("c"), "gamma")
