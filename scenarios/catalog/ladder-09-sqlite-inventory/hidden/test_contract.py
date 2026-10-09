import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class InventoryContract(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db = Path(self.directory.name) / "stock.db"

    def cli(self, *args, db=None):
        return subprocess.run([sys.executable, "-m", "app", "--db", str(db or self.db), *args], capture_output=True, text=True, timeout=10)

    def inventory(self, db=None):
        result = self.cli("list", db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_durable_crud_and_parameterized_skus(self):
        for sku, quantity in [("Ω bolt", "7"), ("a'); DROP TABLE inventory; --", "3"), ("z", "0")]:
            added = self.cli("add", sku, quantity)
            self.assertEqual((added.returncode, added.stdout), (0, ""), added.stderr)
        self.assertEqual(self.cli("adjust", "Ω bolt", "-7").returncode, 0)
        self.assertEqual(self.cli("delete", "z").returncode, 0)
        self.assertEqual(self.inventory(), [{"sku": "a'); DROP TABLE inventory; --", "quantity": 3}, {"sku": "Ω bolt", "quantity": 0}])
        self.assertEqual(self.inventory(db=self.db.with_name("other.db")), [])

    def test_invalid_operations_preserve_inventory(self):
        self.assertEqual(self.cli("add", "sku", "2").returncode, 0)
        before = self.inventory()
        for args in [("adjust", "sku", "-3"), ("add", "sku", "99"), ("add", "negative", "-1"), ("adjust", "missing", "5"), ("delete", "missing"), ("add", "bad", "2.5"), ("add", "", "2")]:
            with self.subTest(args=args):
                result = self.cli(*args)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertTrue(result.stderr)
                self.assertEqual(self.inventory(), before)

    def test_large_quantities_remain_exact_across_processes(self):
        maximum = 2**63 - 1
        huge = 2**100 + 123
        for args in [("add", "edge", str(maximum)), ("adjust", "edge", "1"),
                     ("add", "huge", str(huge)), ("adjust", "huge", str(huge))]:
            result = self.cli(*args)
            self.assertEqual((result.returncode, result.stdout), (0, ""), result.stderr)
        expected = [{"sku": "edge", "quantity": maximum + 1}, {"sku": "huge", "quantity": 2 * huge}]
        actual = self.inventory()
        self.assertEqual(actual, expected)
        self.assertTrue(all(type(row["quantity"]) is int for row in actual))
        rejected = self.cli("adjust", "huge", str(-2 * huge - 1))
        self.assertEqual(rejected.returncode, 2, rejected.stderr)
        self.assertTrue(rejected.stderr)
        self.assertEqual(self.inventory(), expected)

    def test_decimal_inputs_beyond_default_conversion_limit(self):
        digits = "9" * 5000
        for args in [("add", "huge", digits), ("adjust", "huge", "1"),
                     ("add", "padded", "0" * 5000 + "7")]:
            result = self.cli(*args)
            self.assertEqual((result.returncode, result.stdout), (0, ""), result.stderr)
        listed = self.cli("list")
        self.assertEqual(listed.returncode, 0, listed.stderr)
        # A callback checks the JSON number token without Python's decimal-int cap.
        rows = json.loads(listed.stdout, parse_int=lambda token: ("integer", token))
        self.assertEqual(rows, [{"sku": "huge", "quantity": ("integer", "1" + "0" * 5000)},
                                {"sku": "padded", "quantity": ("integer", "7")}])
        changed = self.cli("adjust", "huge", "-" + digits)
        self.assertEqual(changed.returncode, 0, changed.stderr)
        self.assertEqual(self.inventory(), [{"sku": "huge", "quantity": 1}, {"sku": "padded", "quantity": 7}])
