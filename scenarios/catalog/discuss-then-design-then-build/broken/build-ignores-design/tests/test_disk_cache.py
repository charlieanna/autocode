import tempfile
import unittest

from app.disk_cache import DiskCache


class DiskCacheTests(unittest.TestCase):
    def test_a_second_instance_reads_what_the_first_stored(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []
            for _ in range(3):
                DiskCache(directory).lookup("net", lambda: calls.append(1) or {"tld": "net"})
            self.assertEqual([1], calls)


if __name__ == "__main__":
    unittest.main()
