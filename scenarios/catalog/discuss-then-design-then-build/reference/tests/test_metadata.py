import importlib
import json
import os
import tempfile
import unittest
from unittest.mock import patch


class ParseTests(unittest.TestCase):
    def test_parse_reads_grace_and_renewal_limits(self):
        from app.metadata import parse

        raw = json.dumps({"tld": "com", "grace": {"days": "30"}, "limits": {"renew_years": 10}}).encode()
        self.assertEqual({"tld": "com", "grace_days": 30, "max_years": 10}, parse(raw))

    def test_metadata_uses_configured_shared_cache_and_refreshes_at_one_hour(self):
        now = [0.0]
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(os.environ, {"METADATA_CACHE_DIR": directory}),
            patch("time.time", side_effect=lambda: now[0]),
            patch("urllib.request.urlopen") as upstream,
        ):
            from app import metadata

            metadata = importlib.reload(metadata)
            self.addCleanup(importlib.reload, metadata)
            response = upstream.return_value.__enter__.return_value
            response.read.return_value = json.dumps(
                {"tld": "net", "grace": {"days": "30"}, "limits": {"renew_years": 10}}
            ).encode()
            expected = {"tld": "net", "grace_days": 30, "max_years": 10}
            self.assertEqual(expected, metadata.metadata("net"))
            now[0] = 3599.0
            self.assertEqual(expected, metadata.metadata("net"))
            upstream.assert_called_once_with("https://registry.example.test/metadata/net", timeout=10)
            response.read.return_value = json.dumps(
                {"tld": "net", "grace": {"days": "45"}, "limits": {"renew_years": 5}}
            ).encode()
            now[0] = 3600.0
            self.assertEqual({"tld": "net", "grace_days": 45, "max_years": 5}, metadata.metadata("net"))
            self.assertEqual(2, upstream.call_count)


if __name__ == "__main__":
    unittest.main()
