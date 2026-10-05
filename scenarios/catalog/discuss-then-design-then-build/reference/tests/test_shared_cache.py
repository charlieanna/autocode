import tempfile
import unittest
from pathlib import Path

from app.shared_cache import SharedFileCache


class SharedFileCacheTests(unittest.TestCase):
    def test_a_second_worker_reads_what_the_first_fetched(self):
        with tempfile.TemporaryDirectory() as directory:
            fetched = []
            for worker in range(4):
                SharedFileCache(directory, 3600, lambda: 10.0).get_or_fetch(
                    "net", lambda: fetched.append(worker) or {"tld": "net"})
            self.assertEqual([0], fetched)
            self.assertEqual([], [p.name for p in Path(directory).iterdir() if p.suffix == ".tmp"])

    def test_an_entry_expires_after_the_ttl(self):
        with tempfile.TemporaryDirectory() as directory:
            now = [0.0]
            cache = SharedFileCache(directory, 3600, lambda: now[0])
            cache.get_or_fetch("de", lambda: "old")
            now[0] = 3599.0
            self.assertEqual("old", cache.get_or_fetch("de", lambda: "new"))
            now[0] = 3600.0
            self.assertEqual("new", cache.get_or_fetch("de", lambda: "new"))


if __name__ == "__main__":
    unittest.main()
