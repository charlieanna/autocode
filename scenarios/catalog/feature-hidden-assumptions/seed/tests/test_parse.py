import tempfile
import unittest
from pathlib import Path

from envfile import EnvFileError, dump, load


class LoadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, text):
        path = self.dir / name
        path.write_text(text)
        return path

    def test_reads_entries_in_file_order(self):
        path = self.write("a.env", "B=2\nA=1\n")
        self.assertEqual(list(load(path).items()), [("B", "2"), ("A", "1")])

    def test_skips_blank_lines_and_comments(self):
        path = self.write("a.env", "# settings\n\nA=1\n   \n# end\n")
        self.assertEqual(load(path), {"A": "1"})

    def test_repeated_key_keeps_first_value(self):
        path = self.write("a.env", "A=1\nA=2\n")
        self.assertEqual(load(path), {"A": "1"})

    def test_missing_file_is_an_error(self):
        with self.assertRaisesRegex(EnvFileError, "no such file"):
            load(self.dir / "missing.env")

    def test_line_without_equals_is_an_error(self):
        path = self.write("a.env", "A=1\nnot an entry\n")
        with self.assertRaisesRegex(EnvFileError, ":2: expected KEY=VALUE"):
            load(path)


class DumpTests(unittest.TestCase):
    def test_writes_entries_in_order(self):
        self.assertEqual(dump({"B": "2", "A": "1"}), "B=2\nA=1\n")

    def test_sorted(self):
        self.assertEqual(dump({"B": "2", "A": "1"}, sort=True), "A=1\nB=2\n")


if __name__ == "__main__":
    unittest.main()
