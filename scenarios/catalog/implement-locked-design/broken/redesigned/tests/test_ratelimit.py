import unittest

from ratelimit import LimiterRegistry, TokenBucket


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class RateLimitTests(unittest.TestCase):
    def test_bucket_drains_and_refills(self):
        clock = Clock()
        bucket = TokenBucket(2, 1.0, clock)
        self.assertTrue(bucket.try_acquire(2))
        self.assertFalse(bucket.try_acquire())
        clock.now += 1
        self.assertTrue(bucket.try_acquire())

    def test_registry_keys(self):
        registry = LimiterRegistry(1, 1.0, Clock())
        registry.for_key("b")
        registry.for_key("a")
        self.assertEqual(["a", "b"], registry.keys())


if __name__ == "__main__":
    unittest.main()
