import unittest

from app.ttl_cache import TTLCache


class TTLCacheTests(unittest.TestCase):
    def test_an_entry_expires_after_the_ttl(self):
        now = [0.0]
        cache = TTLCache(3600, lambda: now[0])
        cache.get_or_fetch("de", lambda: "old")
        now[0] = 3600.0
        self.assertEqual("new", cache.get_or_fetch("de", lambda: "new"))


if __name__ == "__main__":
    unittest.main()
