import unittest

from integration.check import check


class CheckTests(unittest.TestCase):
    def test_check_combines_handle_and_fetch(self):
        self.assertEqual("handled D / fetched D", check())


if __name__ == "__main__":
    unittest.main()
