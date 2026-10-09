import unittest

from epp.client import RenewClient
from epp.registry import FakeRegistry, Timeout


class RenewTests(unittest.TestCase):
    def test_renew_extends_expiry_by_one_year(self):
        registry = FakeRegistry({"example.com": 2027})
        result = RenewClient(registry).renew("example.com")
        self.assertEqual(1000, result.code)
        self.assertEqual(2028, registry.expiries["example.com"])

    def test_timeout_before_processing_is_retried(self):
        registry = FakeRegistry({"example.com": 2027})
        registry.fail_next("timeout-before")
        RenewClient(registry).renew("example.com")
        self.assertEqual(2028, registry.expiries["example.com"])

    def test_persistent_timeouts_raise(self):
        registry = FakeRegistry({"example.com": 2027})
        registry.fail_next("timeout-before", "timeout-before", "timeout-before")
        with self.assertRaises(Timeout):
            RenewClient(registry).renew("example.com")
        self.assertEqual(2027, registry.expiries["example.com"])


if __name__ == "__main__":
    unittest.main()
