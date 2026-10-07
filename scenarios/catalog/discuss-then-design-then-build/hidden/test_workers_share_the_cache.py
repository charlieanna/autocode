"""Hidden tests: separate worker processes share one metadata cache.

They go through the service's own entry point, app.metadata.metadata(tld), with the
cache directory the deploy configuration names and provisions (METADATA_CACHE_DIR), and count calls
to the upstream at urllib.request.urlopen. They do not depend on the names a design
chose for its cache module; the oracle checks the code against the design separately.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# One worker process: fakes the upstream, counts its calls in argv[1], prints metadata(tld) for argv[2:].
WORKER = r"""
import io, json, sys, urllib.request

class Response(io.BytesIO):
    def __enter__(self):
        return self
    def __exit__(self, *exc):
        return False

def upstream(url, *args, **kwargs):
    tld = str(getattr(url, "full_url", url)).rstrip("/").rsplit("/", 1)[-1]
    with open(sys.argv[1], "a") as calls:
        calls.write(tld + "\n")
    return Response(json.dumps({"tld": tld, "grace": {"days": "30"}, "limits": {"renew_years": 10}}).encode())

urllib.request.urlopen = upstream
from app.metadata import metadata
print(json.dumps([metadata(tld) for tld in sys.argv[2:]]))
"""


class WorkersShareTheCache(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        # Provisioned, as deploy/gunicorn.conf.py says: whether to create a missing directory is the
        # design's call (a live design fell back to a per-worker memo, and its contract tested that).
        self.cache = Path(temp.name) / "cache"
        self.cache.mkdir()
        self.calls = Path(temp.name) / "upstream-calls.txt"
        self.calls.write_text("")

    def worker(self, *tlds):
        """A fresh worker process: one of the four gunicorn workers, or one just recycled."""
        env = {**os.environ, "METADATA_CACHE_DIR": str(self.cache), "PYTHONPATH": str(ROOT),
               "PYTHONDONTWRITEBYTECODE": "1"}
        proc = subprocess.run([sys.executable, "-c", WORKER, str(self.calls), *tlds], cwd=ROOT, env=env,
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(0, proc.returncode, proc.stderr[-2000:])
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def upstream_calls(self):
        return self.calls.read_text().split()

    def test_four_workers_fetch_a_tld_once(self):
        for _ in range(4):
            self.assertEqual([{"tld": "com", "grace_days": 30, "max_years": 10}], self.worker("com"))
        self.assertEqual(["com"], self.upstream_calls())

    def test_each_tld_is_cached_under_its_own_key(self):
        self.assertEqual(["com", "net"], [doc["tld"] for doc in self.worker("com", "net")])
        self.assertEqual(["net", "com"], [doc["tld"] for doc in self.worker("net", "com")])
        self.assertEqual(["com", "net"], sorted(self.upstream_calls()))

    def test_the_cache_lives_in_the_configured_directory(self):
        self.worker("org")
        self.assertTrue(self.cache.is_dir() and any(self.cache.iterdir()),
                        "nothing was written under METADATA_CACHE_DIR")


if __name__ == "__main__":
    unittest.main()
