"""Targeted tests for pr-197: the .at policy must be reachable through policy_for
and the client, not only as a dictionary entry."""

import unittest

from regclient.client import Command, RegistryClient
from regclient.policies import policy_for
from regclient.transport import RegistryError, ScriptedTransport


class AtPolicyThroughTheClient(unittest.TestCase):
    def test_f1_policy_for_resolves_at(self):
        self.assertEqual(2, policy_for("at").max_attempts)
        self.assertEqual(2, policy_for("AT").max_attempts)

    def test_rate_limited_is_not_retried_for_at(self):
        transport = ScriptedTransport(["error:RATE_LIMITED", "ok"])
        with self.assertRaises(RegistryError):
            RegistryClient(transport).send(Command(tld="at", name="example.at", txn_id="txn-1"))
        self.assertEqual(1, transport.calls)


if __name__ == "__main__":
    unittest.main()
