"""Hidden checks: move and remove work, and every refusal comes from the command itself.

One TestCase class per rule, so a run of a single class says which rule a source breaks.
A refusal exits 2, explains itself on stderr, leaves stock.json byte-for-byte unchanged, and
is not argparse's "invalid choice" (which is how the original code, without move/remove, exits 2).
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

STOCK = Path(__file__).resolve().parents[1] / "stock.py"
HOLDING = {"A1": {"bolt": 1}}


class StockCase(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.cwd = Path(self._dir.name)

    def tearDown(self):
        self._dir.cleanup()

    def cli(self, *args):
        return subprocess.run([sys.executable, str(STOCK), *args], cwd=self.cwd, capture_output=True, text=True)

    def store(self, data=None):
        path = self.cwd / "stock.json"
        if data is not None:
            path.write_text(data if isinstance(data, str) else json.dumps(data))
        return path.read_bytes() if path.exists() else None

    def assert_refused(self, *args, data):
        before = self.store(data)
        result = self.cli(*args)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertTrue(result.stderr.strip())
        self.assertNotIn("invalid choice", result.stderr)  # refused by the command, not by argparse
        self.assertEqual(self.store(), before)


class Move(StockCase):
    def test_move_to_new_and_existing_location(self):
        self.store({"A1": {"bolt": 5}, "B2": {"bolt": 1}})
        self.assertEqual(self.cli("move", "bolt", "5", "A1", "B2").returncode, 0)
        self.assertEqual(self.cli("show").stdout, "B2 bolt 6\n")
        self.assertEqual(self.cli("move", "bolt", "1", "B2", "C3").returncode, 0)
        self.assertEqual(self.cli("show").stdout, "B2 bolt 5\nC3 bolt 1\n")


class Remove(StockCase):
    def test_remove_some_keeps_the_rest(self):
        self.store({"A1": {"bolt": 3}})
        self.assertEqual(self.cli("remove", "bolt", "1", "A1").returncode, 0)
        self.assertEqual(self.cli("show").stdout, "A1 bolt 2\n")

    def test_remove_all_drops_holding(self):
        self.store({"A1": {"bolt": 2}})
        self.assertEqual(self.cli("remove", "bolt", "2", "A1").returncode, 0)
        self.assertEqual(self.cli("show").stdout, "")


class RefusesQuantityThatIsNotPositive(StockCase):
    def test_refused(self):
        for args in (("move", "bolt", "0", "A1", "B2"), ("move", "bolt", "-1", "A1", "B2"),
                     ("move", "bolt", "x", "A1", "B2"), ("remove", "bolt", "0", "A1")):
            with self.subTest(args=args):
                self.assert_refused(*args, data=HOLDING)

    def test_non_ascii_digits_are_refused(self):
        # str.isdigit() accepts both: int() raises on a superscript two (a traceback, exit 1) and reads an
        # Arabic-Indic three as 3, which 5 held would let through. README.md: a positive integer in ASCII digits.
        for qty in ("\u00b2", "\u0663"):
            for args in (("move", "bolt", qty, "A1", "B2"), ("remove", "bolt", qty, "A1")):
                with self.subTest(args=args):
                    self.assert_refused(*args, data={"A1": {"bolt": 5}})

    def test_more_digits_than_int_reads_are_refused(self):
        # int() raises past sys.get_int_max_str_digits() (4300 by default): a refusal, never a traceback.
        for args in (("move", "bolt", "9" * 5000, "A1", "B2"), ("remove", "bolt", "9" * 5000, "A1")):
            with self.subTest(args=args[:2]):
                self.assert_refused(*args, data=HOLDING)


class RefusesMoveToSameLocation(StockCase):
    def test_refused(self):
        self.assert_refused("move", "bolt", "1", "A1", "A1", data=HOLDING)


class RefusesTakingMoreThanHeld(StockCase):
    def test_refused(self):
        for args in (("move", "bolt", "2", "A1", "B2"), ("move", "nut", "1", "A1", "B2"),
                     ("move", "bolt", "1", "Z9", "B2"), ("remove", "bolt", "2", "A1"),
                     ("remove", "nut", "1", "A1"), ("remove", "bolt", "1", "Z9")):
            with self.subTest(args=args):
                self.assert_refused(*args, data=HOLDING)


class RefusesMalformedStore(StockCase):
    def test_refused_by_both_commands(self):
        # A JSON true is no quantity, though bool is an int subclass in Python.
        for data in ("{not json", "[1, 2]", '{"A1": {"bolt": 0}}', '{"A1": {"bolt": true}}'):
            for args in (("move", "bolt", "1", "A1", "B2"), ("remove", "bolt", "1", "A1")):
                with self.subTest(data=data, args=args):
                    self.assert_refused(*args, data=data)


if __name__ == "__main__":
    unittest.main()
