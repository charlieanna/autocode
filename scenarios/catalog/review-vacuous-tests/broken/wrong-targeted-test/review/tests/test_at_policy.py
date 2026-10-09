import unittest

from regclient.policies import policy_for


class AtPolicyTests(unittest.TestCase):
    def test_policy_for_resolves_at(self):
        # Wrong expectation: .at allows one retry, so two attempts, not one.
        self.assertEqual(1, policy_for("at").max_attempts)


if __name__ == "__main__":
    unittest.main()
