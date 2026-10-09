import json
import unittest

from app.metadata import parse


class ParseTests(unittest.TestCase):
    def test_parse_reads_grace_and_renewal_limits(self):
        raw = json.dumps({"tld": "com", "grace": {"days": "30"}, "limits": {"renew_years": 10}}).encode()
        self.assertEqual({"tld": "com", "grace_days": 30, "max_years": 10}, parse(raw))


if __name__ == "__main__":
    unittest.main()
