import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from app import import_stock, list_stock


class ImportContract(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "stock.db"
        import_stock(self.path, "sku,qty\na,3\nz,8\n")

    def test_quoted_unicode_rows_upsert_and_zero_quantity(self):
        text = 'sku,qty\n"b, bolt",0\n"line\nbreak",12\n Ω , 7 \na,20\n'
        self.assertEqual(import_stock(self.path, text), 4)
        self.assertEqual(
            list_stock(self.path),
            [
                {"sku": "a", "qty": 20},
                {"sku": "b, bolt", "qty": 0},
                {"sku": "line\nbreak", "qty": 12},
                {"sku": "z", "qty": 8},
                {"sku": "Ω", "qty": 7},
            ],
        )
        before = list_stock(self.path)
        self.assertEqual(import_stock(self.path, "sku,qty\n"), 0)
        self.assertEqual(list_stock(self.path), before)

    def test_late_invalid_row_rolls_back_every_earlier_write(self):
        before = list_stock(self.path)
        invalid_rows = [
            "b,-1\n",
            "b,1.5\n",
            "b,+2\n",
            "b,３\n",
            "b,\n",
            ",1\n",
            "b,2,extra\n",
            "b\n",
            "a,3\n",
            '"unterminated,2\n',
        ]
        for bad in invalid_rows:
            with self.subTest(row=bad):
                with self.assertRaises(ValueError):
                    import_stock(self.path, "sku,qty\na,99\nnew,4\n" + bad)
                self.assertEqual(list_stock(self.path), before)

    def test_invalid_header_is_untouched(self):
        before = list_stock(self.path)
        for text in ("", "qty,sku\n2,b\n", "sku,qty,other\nb,2,x\n"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                import_stock(self.path, text)
        self.assertEqual(list_stock(self.path), before)

    def test_quantities_above_sqlite_integer_range(self):
        self.assertEqual(import_stock(self.path, "sku,qty\na,0009223372036854775808\nbig,9223372036854775809\n"), 2)
        expected = [{"sku": "a", "qty": 2**63}, {"sku": "big", "qty": 2**63 + 1}, {"sku": "z", "qty": 8}]
        self.assertEqual(list_stock(self.path), expected)
        reopened = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; from app import list_stock; assert list_stock(sys.argv[1]) == "
                "[{'sku':'a','qty':2**63},{'sku':'big','qty':2**63+1},{'sku':'z','qty':8}]",
                str(self.path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(reopened.returncode, 0, reopened.stderr)
        with self.assertRaises(ValueError):
            import_stock(self.path, "sku,qty\na,1\nnew,9223372036854775808\nbad,-1\n")
        self.assertEqual(list_stock(self.path), expected)
        self.assertEqual(import_stock(self.path, "sku,qty\na,4\n"), 1)
        self.assertEqual(list_stock(self.path), [dict(expected[0], qty=4), *expected[1:]])

    def test_5000_digit_quantity_round_trip_and_atomic_rejection(self):
        digits = "1" + "0" * 4999
        huge = 10**4999
        self.assertEqual(import_stock(self.path, "sku,qty\na,000" + digits + "\nhuge," + digits + "\n"), 2)
        expected = [{"sku": "a", "qty": huge}, {"sku": "huge", "qty": huge}, {"sku": "z", "qty": 8}]
        self.assertEqual(list_stock(self.path), expected)
        reopened = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; from app import list_stock; assert list_stock(sys.argv[1]) == "
                "[{'sku':'a','qty':10**4999},{'sku':'huge','qty':10**4999},{'sku':'z','qty':8}]",
                str(self.path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(reopened.returncode, 0, reopened.stderr)
        for bad in ("bad,1x\n", "a,2\n", '"unterminated,3\n'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                import_stock(self.path, "sku,qty\na,7\nnew," + digits + "\n" + bad)
            self.assertEqual(list_stock(self.path), expected)
