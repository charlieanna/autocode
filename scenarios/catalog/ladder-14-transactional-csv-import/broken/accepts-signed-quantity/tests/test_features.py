from pathlib import Path
import tempfile
import unittest
from app import import_stock, list_stock

class ImportTests(unittest.TestCase):
    def test_import_and_replace_quantity(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "stock.db"
            self.assertEqual(import_stock(path, "sku,qty\na,2\nb,3\n"), 2)
            self.assertEqual(import_stock(path, "sku,qty\na,7\n"), 1)
            self.assertEqual(list_stock(path), [{"sku": "a", "qty": 7}, {"sku": "b", "qty": 3}])
