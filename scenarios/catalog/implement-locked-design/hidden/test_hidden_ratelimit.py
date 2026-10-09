"""Behavior through the interfaces the approved design specifies."""

import unittest

from ratelimit import LimiterRegistry, TokenBucket
from ratelimit.bucket import TokenBucket as BucketFromModule
from ratelimit.registry import LimiterRegistry as RegistryFromModule


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class BucketTests(unittest.TestCase):
    def test_exports_are_the_module_classes(self):
        self.assertIs(TokenBucket, BucketFromModule)
        self.assertIs(LimiterRegistry, RegistryFromModule)

    def test_starts_full_and_drains(self):
        clock = FakeClock()
        bucket = TokenBucket(capacity=3, refill_per_second=1.0, clock=clock)
        self.assertEqual(3, bucket.available)
        self.assertTrue(bucket.try_acquire())
        self.assertTrue(bucket.try_acquire(2))
        self.assertFalse(bucket.try_acquire())
        self.assertEqual(0, bucket.available)

    def test_failed_acquire_takes_nothing(self):
        clock = FakeClock()
        bucket = TokenBucket(2, 1.0, clock)
        self.assertFalse(bucket.try_acquire(3))
        self.assertEqual(2, bucket.available)

    def test_refills_by_elapsed_time_capped_at_capacity(self):
        clock = FakeClock()
        bucket = TokenBucket(capacity=10, refill_per_second=2.0, clock=clock)
        self.assertTrue(bucket.try_acquire(10))
        clock.advance(1.5)
        self.assertAlmostEqual(3.0, bucket.available)
        self.assertTrue(bucket.try_acquire(3))
        self.assertFalse(bucket.try_acquire(1))
        clock.advance(100)
        self.assertEqual(10, bucket.available)

    def test_only_the_injected_clock_is_used(self):
        clock = FakeClock()
        bucket = TokenBucket(1, 1000.0, clock)
        self.assertTrue(bucket.try_acquire())
        # Real time passes here in the wall-clock sense (the test runs), but the fake clock stands still.
        self.assertFalse(bucket.try_acquire())
        self.assertEqual(0, bucket.available)


class RegistryTests(unittest.TestCase):
    def test_one_bucket_per_key(self):
        clock = FakeClock()
        registry = LimiterRegistry(capacity=2, refill_per_second=1.0, clock=clock)
        a, b = registry.for_key("a"), registry.for_key("b")
        self.assertIs(a, registry.for_key("a"))
        self.assertIsNot(a, b)
        self.assertIsInstance(a, TokenBucket)
        self.assertTrue(a.try_acquire(2))
        self.assertFalse(a.try_acquire())
        self.assertTrue(b.try_acquire())
        self.assertEqual(["a", "b"], registry.keys())

    def test_buckets_share_the_registry_clock(self):
        clock = FakeClock()
        registry = LimiterRegistry(1, 1.0, clock)
        bucket = registry.for_key("k")
        self.assertTrue(bucket.try_acquire())
        clock.advance(1)
        self.assertTrue(bucket.try_acquire())


if __name__ == "__main__":
    unittest.main()
