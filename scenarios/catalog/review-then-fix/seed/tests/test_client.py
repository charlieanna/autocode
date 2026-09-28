import unittest

from regclient.client import Command, RegistryClient
from regclient.transport import RegistryError, ScriptedTransport


def command(tld="com"):
    return Command(tld=tld, name=f"example.{tld}", txn_id="txn-1")


class ClientTests(unittest.TestCase):
    def test_success_on_first_attempt(self):
        transport = ScriptedTransport(["ok"])
        self.assertTrue(RegistryClient(transport).send(command()).ok)
        self.assertEqual(1, transport.calls)

    def test_server_busy_is_retried(self):
        transport = ScriptedTransport(["error:SERVER_BUSY", "ok"])
        self.assertTrue(RegistryClient(transport).send(command()).ok)
        self.assertEqual(2, transport.calls)

    def test_non_retryable_error_is_raised(self):
        transport = ScriptedTransport(["error:INVALID_DOMAIN"])
        with self.assertRaises(RegistryError):
            RegistryClient(transport).send(command())
        self.assertEqual(1, transport.calls)

    def test_timeout_before_execution_is_retried(self):
        transport = ScriptedTransport(["timeout", "ok"])
        self.assertTrue(RegistryClient(transport).send(command()).ok)
        self.assertEqual(["txn-1"], transport.executed)


if __name__ == "__main__":
    unittest.main()
