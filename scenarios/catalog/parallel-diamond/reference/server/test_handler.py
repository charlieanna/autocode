import unittest

from server.handler import handle


class HandleTests(unittest.TestCase):
    def test_handle_names_the_node(self):
        self.assertEqual("handled B", handle("B"))


if __name__ == "__main__":
    unittest.main()
