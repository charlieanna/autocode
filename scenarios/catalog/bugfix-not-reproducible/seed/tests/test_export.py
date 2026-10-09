import unittest

from csvx import export


class ExportTests(unittest.TestCase):
    def test_header_and_rows(self):
        text = export([{"a": 1, "b": "x"}, {"a": 2, "b": "y"}], ["a", "b"])
        self.assertEqual("a,b\n1,x\n2,y\n", text)

    def test_missing_and_none_cells_are_empty(self):
        text = export([{"a": None}, {}], ["a", "b"])
        self.assertEqual("a,b\n,\n,\n", text)

    def test_quotes_commas(self):
        self.assertEqual('a\n"x,y"\n', export([{"a": "x,y"}], ["a"]))


if __name__ == "__main__":
    unittest.main()
