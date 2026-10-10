"""One logical renew must extend the expiry exactly once, whatever the timing of a lost reply."""

import threading
import unittest

from epp.client import RenewClient
from epp.registry import FakeRegistry, Timeout


class ExactlyOnce(unittest.TestCase):
    def renew(self, *faults, attempts=3):
        registry = FakeRegistry({"example.com": 2027})
        registry.fail_next(*faults)
        result = RenewClient(registry, max_attempts=attempts).renew("example.com")
        return registry, result

    def test_normal(self):
        registry, result = self.renew()
        self.assertEqual(2028, registry.expiries["example.com"])
        self.assertEqual(1, len(registry.mutations))
        self.assertEqual(2028, result.expiry_year)

    def test_timeout_before_processing(self):
        registry, result = self.renew("timeout-before")
        self.assertEqual(2028, registry.expiries["example.com"])
        self.assertEqual(1, len(registry.mutations))

    def test_timeout_after_processing(self):
        registry, result = self.renew("timeout-after")
        self.assertEqual(2028, registry.expiries["example.com"], "the renew was applied twice")
        self.assertEqual(1, len(registry.mutations))
        self.assertEqual(1000, result.code)

    def test_two_lost_replies_after_processing(self):
        registry, result = self.renew("timeout-after", "timeout-after")
        self.assertEqual(2028, registry.expiries["example.com"])
        self.assertEqual(1, len(registry.mutations))

    def test_timeout_before_then_after(self):
        registry, result = self.renew("timeout-before", "timeout-after")
        self.assertEqual(2028, registry.expiries["example.com"])
        self.assertEqual(1, len(registry.mutations))

    def test_persistent_timeouts_before_processing_raise_without_a_mutation(self):
        registry = FakeRegistry({"example.com": 2027})
        registry.fail_next("timeout-before", "timeout-before", "timeout-before")
        with self.assertRaises(Timeout):
            RenewClient(registry, max_attempts=3).renew("example.com")
        self.assertEqual(2027, registry.expiries["example.com"])

    def test_concurrent_renews_of_different_domains(self):
        registry = FakeRegistry({f"d{i}.com": 2027 for i in range(8)})
        registry.fail_next(*(["timeout-after"] * 8))
        client = RenewClient(registry)
        threads = [threading.Thread(target=client.renew, args=(f"d{i}.com",)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual({f"d{i}.com": 2028 for i in range(8)}, registry.expiries)
        self.assertEqual(8, len(registry.mutations))


if __name__ == "__main__":
    unittest.main()
