import unittest

from regclient.client import Command, RegistryClient
from regclient.transport import RegistryError, ScriptedTransport, Timeout


def command(tld="com"):
    return Command(tld=tld, name=f"example.{tld}", txn_id="txn-1")


def client(transport):
    return RegistryClient(transport, sleep=lambda seconds: None)


class ClientTests(unittest.TestCase):
    def test_success_on_first_attempt(self):
        transport = ScriptedTransport(["ok"])
        self.assertTrue(client(transport).send(command()).ok)
        self.assertEqual(1, transport.calls)

    def test_server_busy_is_retried(self):
        transport = ScriptedTransport(["error:SERVER_BUSY", "ok"])
        self.assertTrue(client(transport).send(command()).ok)
        self.assertEqual(2, transport.calls)

    def test_non_retryable_error_is_raised(self):
        transport = ScriptedTransport(["error:INVALID_DOMAIN"])
        with self.assertRaises(RegistryError):
            client(transport).send(command())
        self.assertEqual(1, transport.calls)

    def test_timeout_before_execution_is_retried(self):
        transport = ScriptedTransport(["timeout", "ok"])
        self.assertTrue(client(transport).send(command()).ok)
        self.assertEqual(["txn-1"], transport.executed)

    def test_timeout_after_execution_is_not_resent(self):
        transport = ScriptedTransport(["timeout-after", "ok"])
        self.assertTrue(client(transport).send(command()).ok)
        self.assertEqual(["txn-1"], transport.executed)

    def test_com_retries_connection_reset(self):
        transport = ScriptedTransport(["error:CONNECTION_RESET", "ok"])
        self.assertTrue(client(transport).send(command("com")).ok)
        self.assertEqual(2, transport.calls)

    def test_org_does_not_retry_connection_reset(self):
        transport = ScriptedTransport(["error:CONNECTION_RESET", "ok"])
        with self.assertRaises(RegistryError):
            client(transport).send(command("org"))

    def test_backoff_is_applied_between_attempts(self):
        waits = []
        transport = ScriptedTransport(["error:SERVER_BUSY", "ok"])
        RegistryClient(transport, sleep=waits.append).send(command("org"))
        self.assertEqual([1.0], waits)


if __name__ == "__main__":
    unittest.main()
