import unittest

from farewell import farewell, main


class TestFarewellFormal(unittest.TestCase):
    def test_formal_farewell(self):
        self.assertEqual(farewell("Ada", formal=True), "Good evening, Ada.")

    def test_formal_needs_exactly_one_name(self):
        self.assertEqual(main(["--formal"]), 2)


if __name__ == "__main__":
    unittest.main()
