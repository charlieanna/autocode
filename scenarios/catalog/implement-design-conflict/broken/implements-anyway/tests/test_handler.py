import unittest

from api.handler import Handler
from ratelimit import RateLimited, TokenBucket


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class HandlerTests(unittest.TestCase):
    def test_limits_per_client(self):
        handler = Handler(Clock(), capacity=2)
        self.assertEqual(200, handler.handle("a", "r1")[0])
        self.assertEqual(200, handler.handle("a", "r2")[0])
        self.assertEqual(429, handler.handle("a", "r3")[0])
        self.assertEqual(200, handler.handle("b", "r1")[0])

    def test_try_acquire_raises_when_denied(self):
        bucket = TokenBucket(1, 1.0, Clock())
        self.assertIsNone(bucket.try_acquire())
        with self.assertRaises(RateLimited):
            bucket.try_acquire()


if __name__ == "__main__":
    unittest.main()
