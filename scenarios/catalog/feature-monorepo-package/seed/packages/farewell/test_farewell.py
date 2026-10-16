import unittest

from farewell import farewell, main


class TestFarewell(unittest.TestCase):
    def test_farewell(self):
        self.assertEqual(farewell("Ada"), "Goodbye, Ada")

    def test_needs_exactly_one_name(self):
        self.assertEqual(main([]), 2)
        self.assertEqual(main(["Ada", "Lovelace"]), 2)


if __name__ == "__main__":
    unittest.main()
