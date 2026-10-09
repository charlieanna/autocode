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
        self.assertEqual(("RATE_LIMITED", "SERVER_BUSY", "SESSION_LIMIT_EXCEEDED"), policy_for("com").retryable_errors)

    def test_de_gets_one_attempt_and_retries_nothing(self):
        self.assertEqual((1, ()), (policy_for("de").max_attempts, policy_for("DE").retryable_errors))


if __name__ == "__main__":
    unittest.main()
