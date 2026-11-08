import fcntl
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app.shared_cache import SharedFileCache


class SharedFileCacheTests(unittest.TestCase):
    def test_a_second_worker_reads_what_the_first_fetched(self):
        with tempfile.TemporaryDirectory() as directory:
            fetched = []
            for worker in range(4):
                SharedFileCache(directory, 3600, lambda: 10.0).get_or_fetch(
                    "net", lambda: fetched.append(worker) or {"tld": "net"}
                )
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

    def test_missing_nested_directory_and_corrupt_entry_are_replaced_and_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            nested = Path(directory) / "not-yet-created" / "cache"
            cache = SharedFileCache(nested, 60, lambda: 10.0)
            self.assertEqual({"value": "first"}, cache.get_or_fetch("net", lambda: {"value": "first"}))
            self.assertTrue((nested / "net.json").is_file())
            (nested / "net.json").write_text("{broken")
            fetch = Mock(return_value={"value": "replacement"})
            self.assertEqual({"value": "replacement"}, cache.get_or_fetch("net", fetch))
            self.assertEqual({"value": "replacement"}, cache.get_or_fetch("net", fetch))
            fetch.assert_called_once_with()
            self.assertEqual([], list(nested.glob("*.tmp")))

    def test_fresh_hit_returns_without_waiting_for_a_writer_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = SharedFileCache(directory, 60, lambda: 10.0)
            self.assertEqual("already cached", cache.get_or_fetch("net", lambda: "already cached"))
            with patch("fcntl.flock", side_effect=AssertionError("fresh hits must not acquire a writer lock")):
                self.assertEqual("already cached", cache.get_or_fetch("net", lambda: self.fail("fresh hit fetched")))

    def test_miss_rechecks_after_lock_when_another_worker_has_published(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = SharedFileCache(directory, 60, lambda: 10.0)
            value = {"worker": "winner"}

            def publish_at_lock(lock, mode):
                self.assertEqual(fcntl.LOCK_EX, mode)
                self.assertFalse(lock.closed)
                (Path(directory) / "net.json").write_text(json.dumps({"fetched_at": 10.0, "value": value}))

            with patch("fcntl.flock", side_effect=publish_at_lock) as acquire:
                self.assertEqual(value, cache.get_or_fetch("net", lambda: self.fail("another worker already fetched")))
                acquire.assert_called_once()


if __name__ == "__main__":
    unittest.main()
