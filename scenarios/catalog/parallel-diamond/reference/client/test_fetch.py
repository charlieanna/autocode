import unittest

from client.fetch import fetch


class FetchTests(unittest.TestCase):
    def test_fetch_names_the_node(self):
        self.assertEqual("fetched C", fetch("C"))


if __name__ == "__main__":
    unittest.main()
