import unittest

from regclient.policies import DEFAULT, policy_for


class PolicyTests(unittest.TestCase):
    def test_known_registries_have_their_own_policy(self):
        self.assertEqual(4, policy_for("com").max_attempts)
        self.assertEqual(4, policy_for("NET").max_attempts)
        self.assertEqual(1.0, policy_for("org").backoff_seconds)

    def test_unknown_registry_gets_default(self):
        self.assertIs(DEFAULT, policy_for("xyz"))

    def test_retryable_errors_are_deduplicated_and_sorted(self):
        self.assertEqual(("CONNECTION_RESET", "RATE_LIMITED", "SERVER_BUSY"), policy_for("com").retryable_errors)


if __name__ == "__main__":
    unittest.main()
