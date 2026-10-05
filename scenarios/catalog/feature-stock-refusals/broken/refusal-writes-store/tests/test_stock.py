import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

STOCK = Path(__file__).resolve().parents[1] / "stock.py"


class StockCase(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.cwd = Path(self._dir.name)

    def tearDown(self):
        self._dir.cleanup()

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(STOCK), *args], cwd=self.cwd, capture_output=True, text=True)

    def write_store(self, data):
        (self.cwd / "stock.json").write_text(json.dumps(data) if not isinstance(data, str) else data)

    def store_bytes(self):
        path = self.cwd / "stock.json"
        return path.read_bytes() if path.exists() else None


class ReceiveTests(StockCase):
    def test_receive_adds_units(self):
        self.write_store({"A1": {"bolt": 3}})
        result = self.run_cli("receive", "bolt", "2", "A1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.run_cli("show").stdout, "A1 bolt 5\n")

    def test_receive_zero_is_refused_and_store_unchanged(self):
        self.write_store({"A1": {"bolt": 3}})
        before = self.store_bytes()
        result = self.run_cli("receive", "bolt", "0", "A1")
        self.assertEqual(result.returncode, 2)
        self.assertTrue(result.stderr.strip())
        self.assertEqual(self.store_bytes(), before)


class MoveRemoveTests(StockCase):
    def assert_refused(self, result, before):
        self.assertEqual(result.returncode, 2)
        # The refusal must come from move/remove itself. On the original code argparse also exits 2
        # ("invalid choice", after a "usage:" line) and never touches the store.
        self.assertTrue(result.stderr.startswith("stock.py: "), result.stderr)
        self.assertNotIn("invalid choice", result.stderr)
        self.assertEqual(self.store_bytes(), before)

    def test_c1_move_transfers_units(self):
        self.write_store({"A1": {"bolt": 5}})
        result = self.run_cli("move", "bolt", "2", "A1", "B2")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.run_cli("show").stdout, "A1 bolt 3\nB2 bolt 2\n")

    def test_c2_remove_takes_units_and_drops_empty_holdings(self):
        self.write_store({"A1": {"bolt": 2, "nut": 1}})
        result = self.run_cli("remove", "bolt", "2", "A1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.run_cli("show").stdout, "A1 nut 1\n")

    def test_c3_move_more_than_on_hand_is_refused(self):
        self.write_store({"A1": {"bolt": 1}})
        before = self.store_bytes()
        result = self.run_cli("move", "bolt", "2", "A1", "B2")
        self.assert_refused(result, before)

    def test_c4_move_to_same_location_is_refused(self):
        self.write_store({"A1": {"bolt": 1}})
        before = self.store_bytes()
        result = self.run_cli("move", "bolt", "1", "A1", "A1")
        self.assert_refused(result, before)

    def test_c5_malformed_store_is_refused(self):
        self.write_store("{not json")
        before = self.store_bytes()
        result = self.run_cli("remove", "bolt", "1", "A1")
        self.assert_refused(result, before)

    def test_c6_non_positive_quantity_is_refused(self):
        self.write_store({"A1": {"bolt": 1}})
        before = self.store_bytes()
        result = self.run_cli("move", "bolt", "0", "A1", "B2")
        self.assert_refused(result, before)


if __name__ == "__main__":
    unittest.main()
