"""The PR's intended behavior lands, and neither planted regression survives."""
import unittest

from regclient.client import Command, RegistryClient
from regclient.policies import policy_for
from regclient.transport import RegistryError, ScriptedTransport, Timeout


def send(tld, script, waits=None):
    transport = ScriptedTransport(script)
    sleep = waits.append if waits is not None else (lambda seconds: None)
    return transport, RegistryClient(transport, sleep=sleep).send(Command(tld=tld, name=f"x.{tld}", txn_id="t-9"))


class HiddenTests(unittest.TestCase):
    def test_a_command_executed_before_its_reply_was_lost_is_not_resent(self):
        for tld in ("com", "org", "xyz"):
            with self.subTest(tld=tld):
                transport, response = send(tld, ["timeout-after", "ok", "ok"])
                self.assertTrue(response.ok)
                self.assertEqual(["t-9"], transport.executed)
                self.assertEqual(1, transport.calls)

    def test_de_gets_exactly_one_attempt(self):
        for script, error in ((["error:SERVER_BUSY", "ok"], RegistryError), (["timeout", "ok"], Timeout),
                              (["error:RATE_LIMITED", "ok"], RegistryError)):
            with self.subTest(script=script[0]):
                transport = ScriptedTransport(script)
                with self.assertRaises(error):
                    RegistryClient(transport, sleep=lambda s: None).send(Command("DE", "x.de", "t-9"))
                self.assertEqual(1, transport.calls)

    def test_the_prs_registry_policies_are_kept(self):
        self.assertEqual(4, policy_for("net").max_attempts)
        transport, response = send("com", ["error:SESSION_LIMIT_EXCEEDED", "error:SESSION_LIMIT_EXCEEDED", "ok"])
        self.assertTrue(response.ok)
        waits = []
        send("org", ["error:SERVER_BUSY", "error:SERVER_BUSY", "ok"], waits)
        self.assertEqual([1.0, 1.0], waits)


if __name__ == "__main__":
    unittest.main()
