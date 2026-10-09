import unittest

from regclient.policies import POLICIES


class AtPolicyTests(unittest.TestCase):
    def test_f1_at_entry_exists(self):
        entry = POLICIES.get("AT") or POLICIES.get("at")
        self.assertEqual(2, entry.max_attempts)


if __name__ == "__main__":
    unittest.main()
