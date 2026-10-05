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


if __name__ == "__main__":
    unittest.main()
