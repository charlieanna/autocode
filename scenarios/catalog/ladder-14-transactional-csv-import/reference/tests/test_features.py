import tempfile
import unittest
from pathlib import Path

from app import import_stock, list_stock


class ImportTests(unittest.TestCase):
    def test_import_and_replace_quantity(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "stock.db"
            self.assertEqual(import_stock(path, "sku,qty\na,2\nb,3\n"), 2)
            self.assertEqual(import_stock(path, "sku,qty\na,7\n"), 1)
            self.assertEqual(list_stock(path), [{"sku": "a", "qty": 7}, {"sku": "b", "qty": 3}])

    def test_large_quantities_persist_and_invalid_import_rolls_back(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "stock.db"
            digits = "9" * 5000
            self.assertEqual(import_stock(path, "sku,qty\nlarge,9223372036854775808\nhuge," + digits + "\n"), 2)
            expected = [{"sku": "huge", "qty": 10 ** 5000 - 1}, {"sku": "large", "qty": 2 ** 63}]
            self.assertEqual(list_stock(path), expected)
            with self.assertRaises(ValueError):
                import_stock(path, "sku,qty\nhuge,1\nnew," + digits + "\nbad,-1\n")
            self.assertEqual(list_stock(path), expected)
